from collections import deque
from datetime import datetime
from functools import partial

import h5py
import jungfrau_utils as ju
import numpy as np
from bokeh.io import curdoc
from bokeh.layouts import column, gridplot, row
from bokeh.models import (
    BasicTicker,
    BasicTickFormatter,
    BoxZoomTool,
    Button,
    Circle,
    ColumnDataSource,
    CustomJSHover,
    DataRange1d,
    DatetimeAxis,
    Grid,
    HoverTool,
    Legend,
    Line,
    LinearAxis,
    Panel,
    PanTool,
    Plot,
    Range1d,
    ResetTool,
    SaveTool,
    Slider,
    Spacer,
    Tabs,
    TapTool,
    Title,
    Toggle,
    WheelZoomTool,
)
from bokeh.palettes import Reds9  # pylint: disable=E0611
from bokeh.transform import linear_cmap
from tornado import gen

import receiver
import streamvis as sv

doc = curdoc()
doc.title = receiver.args.page_title

# initial image size to organize placeholders for actual data
image_size_x = 1
image_size_y = 1

sv_rt = sv.Runtime()

current_gain_file = ''
current_pedestal_file = ''
jf_calib = None

connected = False

# Currently, it's possible to control only a canvas size, but not a size of the plotting area.
MAIN_CANVAS_WIDTH = 2200 + 55
MAIN_CANVAS_HEIGHT = 1900 + 64

AGGR_CANVAS_WIDTH = 850 + 55
AGGR_CANVAS_HEIGHT = 760 + 30
AGGR_PROJ_X_CANVAS_HEIGHT = 150 + 11
AGGR_PROJ_Y_CANVAS_WIDTH = 150 + 31

APP_FPS = 1
STREAM_ROLLOVER = 36000
HITRATE_ROLLOVER = 1200
image_buffer = deque(maxlen=60)

# Resolution rings positions in angstroms
RESOLUTION_RINGS_POS = np.array([2, 2.2, 2.6, 3, 5, 10])

# Custom tick formatter for displaying large numbers
tick_formatter = BasicTickFormatter(precision=1)


# Main plot
sv_mainview = sv.ImageView(plot_height=MAIN_CANVAS_HEIGHT, plot_width=MAIN_CANVAS_WIDTH)
sv_mainview.toolbar_location = 'below'

# ---- tools
experiment_params = ColumnDataSource(
    data=dict(
        detector_distance=[np.nan],
        beam_energy=[np.nan],
        beam_center_x=[np.nan],
        beam_center_y=[np.nan],
    )
)

resolution_formatter = CustomJSHover(
    args=dict(params=experiment_params),
    code="""
        var detector_distance = params.data.detector_distance
        var beam_energy = params.data.beam_energy
        var beam_center_x = params.data.beam_center_x
        var beam_center_y = params.data.beam_center_y

        var x = special_vars.x - beam_center_x
        var y = special_vars.y - beam_center_y

        var theta = Math.atan(Math.sqrt(x*x + y*y) * 75e-6 / detector_distance) / 2
        var resolution = 6200 / beam_energy / Math.sin(theta)  // 6200 = 1.24 / 2 / 1e-4

        return resolution.toFixed(2)
    """,
)

hovertool = HoverTool(
    tooltips=[("intensity", "@image"), ("resolution", "@x{resolution} Å")],
    formatters=dict(x=resolution_formatter),
    names=['image_glyph'],
)

# replace the existing HoverTool
sv_mainview.plot.tools[-1] = hovertool

# ---- peaks circle glyph
main_image_peaks_source = ColumnDataSource(dict(x=[], y=[]))
sv_mainview.plot.add_glyph(
    main_image_peaks_source,
    Circle(x='x', y='y', size=15, fill_alpha=0, line_width=3, line_color='white'),
)


# Total sum intensity plot
main_sum_intensity_plot = Plot(
    x_range=DataRange1d(),
    y_range=DataRange1d(),
    plot_height=200,
    plot_width=1350,
    toolbar_location='below',
)

# ---- tools
main_sum_intensity_plot.toolbar.logo = None
main_sum_intensity_plot.add_tools(
    PanTool(), BoxZoomTool(), WheelZoomTool(dimensions='width'), ResetTool()
)

# ---- axes
main_sum_intensity_plot.add_layout(
    LinearAxis(axis_label="Total intensity", formatter=tick_formatter), place='left'
)
main_sum_intensity_plot.add_layout(DatetimeAxis(), place='below')

# ---- grid lines
main_sum_intensity_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
main_sum_intensity_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
main_sum_intensity_source = ColumnDataSource(dict(x=[], y=[]))
main_sum_intensity_plot.add_glyph(main_sum_intensity_source, Line(x='x', y='y'))


# Aggr total sum intensity plot
aggr_sum_intensity_plot = Plot(
    x_range=DataRange1d(),
    y_range=DataRange1d(),
    plot_height=200,
    plot_width=1350,
    toolbar_location='below',
)

# ---- tools
aggr_sum_intensity_plot.toolbar.logo = None
aggr_sum_intensity_plot.add_tools(
    PanTool(), BoxZoomTool(), WheelZoomTool(dimensions='width'), ResetTool()
)

# ---- axes
aggr_sum_intensity_plot.add_layout(
    LinearAxis(axis_label="Zoom total intensity", formatter=tick_formatter), place='left'
)
aggr_sum_intensity_plot.add_layout(DatetimeAxis(), place='below')

# ---- grid lines
aggr_sum_intensity_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
aggr_sum_intensity_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
aggr_sum_intensity_source = ColumnDataSource(dict(x=[], y=[]))
aggr_sum_intensity_plot.add_glyph(aggr_sum_intensity_source, Line(x='x', y='y'))


# Intensity stream reset button
def sum_intensity_reset_button_callback():
    stream_t = datetime.now()  # keep the latest point in order to prevent full axis reset
    main_sum_intensity_source.data.update(x=[stream_t], y=[main_sum_intensity_source.data['y'][-1]])
    aggr_sum_intensity_source.data.update(x=[stream_t], y=[aggr_sum_intensity_source.data['y'][-1]])


sum_intensity_reset_button = Button(label="Reset", button_type='default')
sum_intensity_reset_button.on_click(sum_intensity_reset_button_callback)


# Aggregation plot
sv_aggrplot = sv.ImageView(plot_height=AGGR_CANVAS_HEIGHT, plot_width=AGGR_CANVAS_WIDTH)
sv_aggrplot.toolbar_location = 'below'

# ---- tools
# replace the existing HoverTool
sv_aggrplot.plot.tools[-1] = hovertool

sv_mainview.add_as_zoom(sv_aggrplot, line_color='white')


# Projection of aggregate image onto x axis
aggr_image_proj_x_plot = Plot(
    x_range=sv_aggrplot.plot.x_range,
    y_range=DataRange1d(),
    plot_height=AGGR_PROJ_X_CANVAS_HEIGHT,
    plot_width=sv_aggrplot.plot.plot_width,
    toolbar_location=None,
)

# ---- axes
aggr_image_proj_x_plot.add_layout(LinearAxis(major_label_orientation='vertical'), place='right')
aggr_image_proj_x_plot.add_layout(LinearAxis(major_label_text_font_size='0pt'), place='below')

# ---- grid lines
aggr_image_proj_x_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
aggr_image_proj_x_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
aggr_image_proj_x_source = ColumnDataSource(
    dict(x=np.arange(image_size_x) + 0.5, y=np.zeros(image_size_x))  # shift to a pixel center
)

aggr_image_proj_x_plot.add_glyph(
    aggr_image_proj_x_source, Line(x='x', y='y', line_color='steelblue', line_width=2)
)


# Projection of aggregate image onto x axis
aggr_image_proj_y_plot = Plot(
    x_range=DataRange1d(),
    y_range=sv_aggrplot.plot.y_range,
    plot_height=sv_aggrplot.plot.plot_height,
    plot_width=AGGR_PROJ_Y_CANVAS_WIDTH,
    toolbar_location=None,
)

# ---- axes
aggr_image_proj_y_plot.add_layout(LinearAxis(), place='above')
aggr_image_proj_y_plot.add_layout(LinearAxis(major_label_text_font_size='0pt'), place='left')

# ---- grid lines
aggr_image_proj_y_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
aggr_image_proj_y_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
aggr_image_proj_y_source = ColumnDataSource(
    dict(x=np.zeros(image_size_y), y=np.arange(image_size_y) + 0.5)  # shift to a pixel center
)

aggr_image_proj_y_plot.add_glyph(
    aggr_image_proj_y_source, Line(x='x', y='y', line_color='steelblue', line_width=2)
)


# Create colormapper
sv_colormapper = sv.ColorMapper([sv_mainview, sv_aggrplot])

# ---- add colorbar to the main plot
sv_colormapper.color_bar.width = MAIN_CANVAS_WIDTH // 2
sv_mainview.plot.add_layout(sv_colormapper.color_bar, place='above')


# Add resolution rings to both plots
sv_resolrings = sv.ResolutionRings([sv_mainview, sv_aggrplot], RESOLUTION_RINGS_POS)


# Add mask to both plots
sv_mask = sv.Mask([sv_mainview, sv_aggrplot])


# Histogram plot
sv_hist = sv.Histogram(nplots=1, plot_height=280, plot_width=700)


# Trajectory plot
trajectory_plot = Plot(
    x_range=DataRange1d(),
    y_range=DataRange1d(),
    plot_height=650,
    plot_width=1380,
    toolbar_location='left',
)

# ---- tools
trajectory_plot.toolbar.logo = None
taptool = TapTool(names=['trajectory_circle'])
trajectory_ht = HoverTool(
    tooltips=[("frame", "@frame"), ("number of spots", "@nspots")], names=['trajectory_circle']
)
trajectory_plot.add_tools(
    PanTool(), BoxZoomTool(), WheelZoomTool(), SaveTool(), ResetTool(), taptool, trajectory_ht
)

# ---- axes
trajectory_plot.add_layout(LinearAxis(), place='below')
trajectory_plot.add_layout(LinearAxis(), place='left')

# ---- grid lines
trajectory_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
trajectory_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
trajectory_line_source = ColumnDataSource(dict(x=[], y=[]))
trajectory_plot.add_glyph(trajectory_line_source, Line(x='x', y='y'))

# ---- trajectory circle glyph and selection callback
circle_mapper = linear_cmap(field_name='nspots', palette=['#ffffff'] + Reds9[::-1], low=0, high=100)
trajectory_circle_source = ColumnDataSource(dict(x=[], y=[], frame=[], nspots=[]))
trajectory_plot.add_glyph(
    trajectory_circle_source,
    Circle(x='x', y='y', fill_color=circle_mapper, size=12),
    selection_glyph=Circle(fill_color=circle_mapper, line_color='blue', line_width=3),
    nonselection_glyph=Circle(fill_color=circle_mapper),
    name='trajectory_circle',
)


def trajectory_circle_source_callback(_attr, _old, new):
    if new:
        sv_rt.current_metadata, sv_rt.current_image = receiver.data_buffer[new[0]]


trajectory_circle_source.selected.on_change('indices', trajectory_circle_source_callback)


# Peakfinder plot
hitrate_plot = Plot(
    title=Title(text='Hitrate'),
    x_range=DataRange1d(),
    y_range=Range1d(0, 1, bounds=(0, 1)),
    plot_height=250,
    plot_width=1380,
    toolbar_location='left',
)

# ---- tools
hitrate_plot.toolbar.logo = None
hitrate_plot.add_tools(PanTool(), BoxZoomTool(), WheelZoomTool(), SaveTool(), ResetTool())

# ---- axes
hitrate_plot.add_layout(DatetimeAxis(), place='below')
hitrate_plot.add_layout(LinearAxis(), place='left')

# ---- grid lines
hitrate_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
hitrate_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- red line glyph
hitrate_line_red_source = ColumnDataSource(dict(x=[], y=[]))
hitrate_red_line = hitrate_plot.add_glyph(
    hitrate_line_red_source, Line(x='x', y='y', line_color='red', line_width=2)
)

# ---- blue line glyph
hitrate_line_blue_source = ColumnDataSource(dict(x=[], y=[]))
hitrate_blue_line = hitrate_plot.add_glyph(
    hitrate_line_blue_source, Line(x='x', y='y', line_color='steelblue', line_width=2)
)

# ---- legend
hitrate_plot.add_layout(
    Legend(
        items=[
            (f"{receiver.hitrate_buffer_fast.maxlen} shots avg", [hitrate_red_line]),
            (f"{receiver.hitrate_buffer_slow.maxlen} shots avg", [hitrate_blue_line]),
        ],
        location='top_left',
    )
)
hitrate_plot.legend.click_policy = "hide"


# Stream panel
# ---- image buffer slider
def image_buffer_slider_callback(_attr, _old, new):
    sv_rt.current_metadata, sv_rt.current_image = image_buffer[new]


image_buffer_slider = Slider(
    start=0, end=59, value=0, step=1, title="Buffered Image", disabled=True
)
image_buffer_slider.on_change('value', image_buffer_slider_callback)

# ---- connect toggle button
def stream_button_callback(state):
    global connected
    if state:
        connected = True
        stream_button.label = 'Connecting'
        stream_button.button_type = 'default'
        image_buffer_slider.disabled = True

    else:
        connected = False
        stream_button.label = 'Connect'
        stream_button.button_type = 'default'
        image_buffer_slider.disabled = False


stream_button = Toggle(label="Connect", button_type='default')
stream_button.on_click(stream_button_callback)

# assemble
tab_stream = Panel(child=column(image_buffer_slider, stream_button), title="Stream")
data_source_tabs = Tabs(tabs=[tab_stream])


# Colormaper panel
colormap_panel = column(
    sv_colormapper.select,
    Spacer(height=10),
    sv_colormapper.scale_radiobuttongroup,
    Spacer(height=10),
    sv_colormapper.auto_toggle,
    sv_colormapper.display_max_spinner,
    sv_colormapper.display_min_spinner,
)


# Show only hits toggle
show_only_hits_toggle = Toggle(label="Show Only Hits", button_type='default')


# Metadata datatable
sv_metadata = sv.MetadataHandler(datatable_height=360, datatable_width=650)


# Custom tabs
layout_intensity = column(
    gridplot(
        [main_sum_intensity_plot, aggr_sum_intensity_plot],
        ncols=1,
        toolbar_location='left',
        toolbar_options=dict(logo=None),
    ),
    row(Spacer(), sum_intensity_reset_button),
)

sv_hist.log10counts_toggle.width = 120
layout_hist = column(
    sv_hist.plots[0],
    row(
        sv_hist.nbins_spinner,
        column(
            Spacer(height=19),
            row(sv_hist.auto_toggle, Spacer(width=10), sv_hist.log10counts_toggle),
        ),
    ),
    row(sv_hist.lower_spinner, sv_hist.upper_spinner),
)

debug_tab = Panel(
    child=column(
        layout_intensity,
        row(
            layout_hist,
            Spacer(width=30),
            column(sv_metadata.datatable, row(Spacer(), sv_metadata.show_all_toggle)),
        ),
    ),
    title="Debug",
)

scan_tab = Panel(child=column(trajectory_plot, hitrate_plot), title="SwissMX")

# assemble
custom_tabs = Tabs(tabs=[debug_tab, scan_tab], height=960, width=1400)


# Final layouts
layout_main = column(sv_mainview.plot)

layout_aggr = column(
    aggr_image_proj_x_plot,
    row(sv_aggrplot.plot, aggr_image_proj_y_plot),
    row(sv_resolrings.toggle, sv_mask.toggle, show_only_hits_toggle),
)

layout_controls = column(sv_metadata.issues_dropdown, colormap_panel, data_source_tabs)

layout_side_panel = column(custom_tabs, row(layout_controls, Spacer(width=30), layout_aggr))

final_layout = row(layout_main, Spacer(width=30), layout_side_panel)

doc.add_root(row(Spacer(width=50), final_layout))


@gen.coroutine
def update_client(image, metadata):
    sv_colormapper.update(image)
    resized_images = sv_mainview.update(image)

    aggr_image = resized_images[1]
    aggr_image_height, aggr_image_width = aggr_image.shape

    aggr_y_start = sv_aggrplot.y_start
    aggr_y_end = sv_aggrplot.y_end
    aggr_x_start = sv_aggrplot.x_start
    aggr_x_end = sv_aggrplot.x_end

    aggr_image_proj_x = aggr_image.mean(axis=0)
    aggr_image_proj_y = aggr_image.mean(axis=1)
    aggr_image_proj_r_y = np.linspace(aggr_y_start, aggr_y_end, aggr_image_height)
    aggr_image_proj_r_x = np.linspace(aggr_x_start, aggr_x_end, aggr_image_width)

    if custom_tabs.tabs[custom_tabs.active].title == "Debug":
        sv_hist.update([aggr_image])

    # Parse metadata
    metadata_toshow = sv_metadata.parse(metadata)

    # Update spots locations
    if 'number_of_spots' in metadata and 'spot_x' in metadata and 'spot_y' in metadata:
        spot_x = metadata['spot_x']
        spot_y = metadata['spot_y']
        if metadata['number_of_spots'] == len(spot_x) == len(spot_y):
            main_image_peaks_source.data.update(x=spot_x, y=spot_y)
        else:
            main_image_peaks_source.data.update(x=[], y=[])
            sv_metadata.add_issue('Spots data is inconsistent')
    else:
        main_image_peaks_source.data.update(x=[], y=[])

    aggr_image_proj_y_source.data.update(x=aggr_image_proj_y, y=aggr_image_proj_r_y)
    aggr_image_proj_x_source.data.update(x=aggr_image_proj_r_x, y=aggr_image_proj_x)

    # Update hover tool experiment parameters
    experiment_params.data.update(
        detector_distance=[metadata.get('detector_distance', np.nan)],
        beam_energy=[metadata.get('beam_energy', np.nan)],
        beam_center_x=[metadata.get('beam_center_x', np.nan)],
        beam_center_y=[metadata.get('beam_center_y', np.nan)],
    )

    # Update total intensities plots
    stream_t = datetime.now()
    main_sum_intensity_source.stream(
        new_data=dict(x=[stream_t], y=[np.sum(image, dtype=np.float)]), rollover=STREAM_ROLLOVER
    )
    aggr_y_start = int(np.floor(aggr_y_start))
    aggr_x_start = int(np.floor(aggr_x_start))
    aggr_y_end = int(np.ceil(aggr_y_end))
    aggr_x_end = int(np.ceil(aggr_x_end))
    aggr_sum_intensity_source.stream(
        new_data=dict(
            x=[stream_t],
            y=[np.sum(image[aggr_y_start:aggr_y_end, aggr_x_start:aggr_x_end], dtype=np.float)],
        ),
        rollover=STREAM_ROLLOVER,
    )

    # Update peakfinder plot
    hitrate_line_red_source.stream(
        new_data=dict(
            x=[stream_t], y=[sum(receiver.hitrate_buffer_fast) / len(receiver.hitrate_buffer_fast)]
        ),
        rollover=HITRATE_ROLLOVER,
    )

    hitrate_line_blue_source.stream(
        new_data=dict(
            x=[stream_t], y=[sum(receiver.hitrate_buffer_slow) / len(receiver.hitrate_buffer_slow)]
        ),
        rollover=HITRATE_ROLLOVER,
    )

    # Update scan positions
    if custom_tabs.tabs[custom_tabs.active].title == "SwissMX" and receiver.peakfinder_buffer:
        peakfinder_buffer = np.array(receiver.peakfinder_buffer)
        trajectory_circle_source.data.update(
            x=peakfinder_buffer[:, 0],
            y=peakfinder_buffer[:, 1],
            frame=peakfinder_buffer[:, 2],
            nspots=peakfinder_buffer[:, 3],
        )

    # Update mask
    sv_mask.update(metadata.get('pedestal_file'), metadata.get('detector_name'), sv_metadata)

    sv_resolrings.update(metadata, sv_metadata)

    sv_metadata.update(metadata_toshow)


@gen.coroutine
def internal_periodic_callback():
    global current_gain_file, current_pedestal_file, jf_calib

    if connected:
        if receiver.state == 'polling':
            stream_button.label = 'Polling'
            stream_button.button_type = 'warning'

        elif receiver.state == 'receiving':
            stream_button.label = 'Receiving'
            stream_button.button_type = 'success'

            if show_only_hits_toggle.active:
                if receiver.last_hit_data != (None, None):
                    sv_rt.current_metadata, sv_rt.current_image = receiver.last_hit_data
            else:
                sv_rt.current_metadata, sv_rt.current_image = receiver.data_buffer[-1]

            if sv_rt.current_image.dtype != np.float16 and sv_rt.current_image.dtype != np.float32:
                gain_file = sv_rt.current_metadata.get('gain_file')
                pedestal_file = sv_rt.current_metadata.get('pedestal_file')
                detector_name = sv_rt.current_metadata.get('detector_name')
                is_correction_data_present = gain_file and pedestal_file and detector_name

                if is_correction_data_present:
                    if current_gain_file != gain_file or current_pedestal_file != pedestal_file:
                        # Update gain/pedestal filenames and JungfrauCalibration
                        current_gain_file = gain_file
                        current_pedestal_file = pedestal_file

                        with h5py.File(current_gain_file, 'r') as h5gain:
                            gain = h5gain['/gains'][:]

                        with h5py.File(current_pedestal_file, 'r') as h5pedestal:
                            pedestal = h5pedestal['/gains'][:]
                            pixel_mask = h5pedestal['/pixel_mask'][:].astype(np.int32)

                        jf_calib = ju.JungfrauCalibration(gain, pedestal, pixel_mask)

                    sv_rt.current_image = jf_calib.apply_gain_pede(sv_rt.current_image)
                    sv_rt.current_image = ju.apply_geometry(sv_rt.current_image, detector_name)
            else:
                sv_rt.current_image = sv_rt.current_image.astype('float32', copy=True)

            if not image_buffer or image_buffer[-1][0] is not sv_rt.current_metadata:
                image_buffer.append((sv_rt.current_metadata, sv_rt.current_image))

            trajectory_circle_source.selected.indices = []

            # Set slider to the right-most position
            if len(image_buffer) > 1:
                image_buffer_slider.end = len(image_buffer) - 1
                image_buffer_slider.value = len(image_buffer) - 1

    if sv_rt.current_image.shape != (1, 1):
        doc.add_next_tick_callback(
            partial(update_client, image=sv_rt.current_image, metadata=sv_rt.current_metadata)
        )


doc.add_periodic_callback(internal_periodic_callback, 1000 / APP_FPS)
