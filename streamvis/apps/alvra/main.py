import os
from datetime import datetime
from functools import partial

import numpy as np
from bokeh.io import curdoc
from bokeh.layouts import column, gridplot, row
from bokeh.models import BasicTicker, BasicTickFormatter, BoxZoomTool, Button, ColumnDataSource, \
    CustomJS, DataRange1d, Dropdown, Grid, Line, LinearAxis, Panel, PanTool, Plot, Quad, Rect, \
    ResetTool, Select, Slider, Spacer, Tabs, TextInput, Title, Toggle, WheelZoomTool
from PIL import Image as PIL_Image
from tornado import gen

import receiver
import streamvis as sv

doc = curdoc()
doc.title = receiver.args.page_title

# Expected image sizes for the detector
IMAGE_SIZE_X = 9216 + (9 - 1) * 6 + 2 * 3 * 9
IMAGE_SIZE_Y = 514

# initial image size to organize placeholders for actual data
image_size_x = IMAGE_SIZE_X
image_size_y = IMAGE_SIZE_Y

current_image = np.zeros((1, 1), dtype='float32')
current_metadata = dict(shape=[image_size_y, image_size_x])
current_stats = None

connected = False

# Currently, it's possible to control only a canvas size, but not a size of the plotting area.
MAIN_CANVAS_WIDTH = 3700 + 55
MAIN_CANVAS_HEIGHT = 514 + 96

ZOOM_CANVAS_WIDTH = 1030 + 55
ZOOM_CANVAS_HEIGHT = 514 + 30

ZOOM_AGG_Y_PLOT_WIDTH = 200
ZOOM_AGG_X_PLOT_HEIGHT = 370
ZOOM_HIST_PLOT_HEIGHT = 280
TOTAL_INT_PLOT_HEIGHT = 200
TOTAL_INT_PLOT_WIDTH = 1150

APP_FPS = 1
stream_t = 0
STREAM_ROLLOVER = 3600

HDF5_FILE_PATH = '/filepath'
HDF5_FILE_PATH_UPDATE_PERIOD = 10000  # ms
HDF5_DATASET_PATH = '/entry/data/data'
hdf5_file_data = lambda pulse: None

ZOOM_INIT_WIDTH = 1030
ZOOM_INIT_HEIGHT = image_size_y
ZOOM1_INIT_X = (ZOOM_INIT_WIDTH + 6) * 2
ZOOM2_INIT_X = (ZOOM_INIT_WIDTH + 6) * 6

current_spectra = None
saved_spectra = dict()

# Custom tick formatter for displaying large numbers
tick_formatter = BasicTickFormatter(precision=1)

# Create colormapper
sv_colormapper = sv.ColorMapper()


# Main plot
sv_mainplot = sv.ImagePlot(
    sv_colormapper,
    plot_height=MAIN_CANVAS_HEIGHT, plot_width=MAIN_CANVAS_WIDTH,
)

# ---- add colorbar
sv_colormapper.color_bar.width = MAIN_CANVAS_WIDTH // 2
sv_colormapper.color_bar.location = (0, -5)
sv_mainplot.plot.add_layout(sv_colormapper.color_bar, place='below')


# Zoom plot 1
sv_zoomplot1 = sv.ImagePlot(
    sv_colormapper,
    plot_height=ZOOM_CANVAS_HEIGHT, plot_width=ZOOM_CANVAS_WIDTH,
)

# ---- add rectangle glyph of zoom area to the main plot
zoom1_area_source = ColumnDataSource(
    dict(x=[ZOOM1_INIT_X + ZOOM_INIT_WIDTH / 2], y=[ZOOM_INIT_HEIGHT / 2],
         width=[ZOOM_INIT_WIDTH], height=[image_size_y]))

rect = Rect(
    x='x', y='y', width='width', height='height', line_color='red', line_width=2, fill_alpha=0)
sv_mainplot.plot.add_glyph(zoom1_area_source, rect)

jscode_move_rect = """
    var data = source.data;
    var start = cb_obj.start;
    var end = cb_obj.end;
    data['%s'] = [start + (end - start) / 2];
    data['%s'] = [end - start];
    source.change.emit();
"""

sv_zoomplot1.plot.x_range.callback = CustomJS(
    args=dict(source=zoom1_area_source), code=jscode_move_rect % ('x', 'width'))

sv_zoomplot1.plot.y_range.callback = CustomJS(
    args=dict(source=zoom1_area_source), code=jscode_move_rect % ('y', 'height'))


# Aggregate zoom1 plot along x axis
zoom1_plot_agg_x = Plot(
    title=Title(text="Zoom Area 1"),
    x_range=sv_zoomplot1.plot.x_range,
    y_range=DataRange1d(),
    plot_height=ZOOM_AGG_X_PLOT_HEIGHT,
    plot_width=sv_zoomplot1.plot.plot_width,
    toolbar_location='left',
)

# ---- tools
zoom1_plot_agg_x.toolbar.logo = None
zoom1_plot_agg_x.add_tools(
    PanTool(dimensions='height'), WheelZoomTool(dimensions='height'), ResetTool())

# ---- axes
zoom1_plot_agg_x.add_layout(LinearAxis(major_label_orientation='vertical'), place='right')
zoom1_plot_agg_x.add_layout(LinearAxis(major_label_text_font_size='0pt'), place='below')

# ---- grid lines
zoom1_plot_agg_x.add_layout(Grid(dimension=0, ticker=BasicTicker()))
zoom1_plot_agg_x.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
zoom1_agg_x_source = ColumnDataSource(
    dict(x=np.arange(image_size_x) + 0.5,  # shift to a pixel center
         y=np.zeros(image_size_x)))

zoom1_plot_agg_x.add_glyph(
    zoom1_agg_x_source, Line(x='x', y='y', line_color='steelblue', line_width=2))


# Aggregate zoom1 plot along y axis
zoom1_plot_agg_y = Plot(
    x_range=DataRange1d(),
    y_range=sv_zoomplot1.plot.y_range,
    plot_height=sv_zoomplot1.plot.plot_height,
    plot_width=ZOOM_AGG_Y_PLOT_WIDTH,
    toolbar_location=None,
)

# ---- axes
zoom1_plot_agg_y.add_layout(LinearAxis(), place='above')
zoom1_plot_agg_y.add_layout(LinearAxis(major_label_text_font_size='0pt'), place='left')

# ---- grid lines
zoom1_plot_agg_y.add_layout(Grid(dimension=0, ticker=BasicTicker()))
zoom1_plot_agg_y.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
zoom1_agg_y_source = ColumnDataSource(
    dict(x=np.zeros(image_size_y),
         y=np.arange(image_size_y) + 0.5))  # shift to a pixel center

zoom1_plot_agg_y.add_glyph(zoom1_agg_y_source, Line(x='x', y='y', line_color='steelblue'))


# Histogram zoom1 plot
zoom1_hist_plot = Plot(
    x_range=DataRange1d(),
    y_range=DataRange1d(),
    plot_height=ZOOM_HIST_PLOT_HEIGHT,
    plot_width=sv_zoomplot1.plot.plot_width,
    toolbar_location='left',
)

# ---- tools
zoom1_hist_plot.toolbar.logo = None
zoom1_hist_plot.add_tools(PanTool(), BoxZoomTool(), WheelZoomTool(), ResetTool())

# ---- axes
zoom1_hist_plot.add_layout(LinearAxis(axis_label="Intensity"), place='below')
zoom1_hist_plot.add_layout(
    LinearAxis(axis_label="Counts", major_label_orientation='vertical'), place='right')

# ---- grid lines
zoom1_hist_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
zoom1_hist_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- quad (single bin) glyph
hist1_source = ColumnDataSource(dict(left=[], right=[], top=[]))
zoom1_hist_plot.add_glyph(
    hist1_source, Quad(left="left", right="right", top="top", bottom=0, fill_color="steelblue"))


# Zoom plot 2
sv_zoomplot2 = sv.ImagePlot(
    sv_colormapper,
    plot_height=ZOOM_CANVAS_HEIGHT, plot_width=ZOOM_CANVAS_WIDTH,
)

# ---- add rectangle glyph of zoom area to the main plot
zoom2_area_source = ColumnDataSource(
    dict(x=[ZOOM2_INIT_X + ZOOM_INIT_WIDTH / 2], y=[ZOOM_INIT_HEIGHT / 2],
         width=[ZOOM_INIT_WIDTH], height=[image_size_y]))

rect = Rect(
    x='x', y='y', width='width', height='height', line_color='green', line_width=2, fill_alpha=0)
sv_mainplot.plot.add_glyph(zoom2_area_source, rect)

jscode_move_rect = """
    var data = source.data;
    var start = cb_obj.start;
    var end = cb_obj.end;
    data['%s'] = [start + (end - start) / 2];
    data['%s'] = [end - start];
    source.change.emit();
"""

sv_zoomplot2.plot.x_range.callback = CustomJS(
    args=dict(source=zoom2_area_source), code=jscode_move_rect % ('x', 'width'))

sv_zoomplot2.plot.y_range.callback = CustomJS(
    args=dict(source=zoom2_area_source), code=jscode_move_rect % ('y', 'height'))


# Aggregate zoom2 plot along x axis
zoom2_plot_agg_x = Plot(
    title=Title(text="Zoom Area 2"),
    x_range=sv_zoomplot2.plot.x_range,
    y_range=DataRange1d(),
    plot_height=ZOOM_AGG_X_PLOT_HEIGHT,
    plot_width=sv_zoomplot2.plot.plot_width,
    toolbar_location='left',
)

# ---- tools
zoom2_plot_agg_x.toolbar.logo = None
zoom2_plot_agg_x.add_tools(zoom1_plot_agg_x.tools[0], zoom1_plot_agg_x.tools[1], ResetTool())

# ---- axes
zoom2_plot_agg_x.add_layout(LinearAxis(major_label_orientation='vertical'), place='right')
zoom2_plot_agg_x.add_layout(LinearAxis(major_label_text_font_size='0pt'), place='below')

# ---- grid lines
zoom2_plot_agg_x.add_layout(Grid(dimension=0, ticker=BasicTicker()))
zoom2_plot_agg_x.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
zoom2_agg_x_source = ColumnDataSource(
    dict(x=np.arange(image_size_x) + 0.5,  # shift to a pixel center
         y=np.zeros(image_size_x)))

zoom2_plot_agg_x.add_glyph(
    zoom2_agg_x_source, Line(x='x', y='y', line_color='steelblue', line_width=2))


# Aggregate zoom2 plot along y axis
zoom2_plot_agg_y = Plot(
    x_range=DataRange1d(),
    y_range=sv_zoomplot2.plot.y_range,
    plot_height=sv_zoomplot2.plot.plot_height,
    plot_width=ZOOM_AGG_Y_PLOT_WIDTH,
    toolbar_location=None,
)

# ---- axes
zoom2_plot_agg_y.add_layout(LinearAxis(), place='above')
zoom2_plot_agg_y.add_layout(LinearAxis(major_label_text_font_size='0pt'), place='left')

# ---- grid lines
zoom2_plot_agg_y.add_layout(Grid(dimension=0, ticker=BasicTicker()))
zoom2_plot_agg_y.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
zoom2_agg_y_source = ColumnDataSource(
    dict(x=np.zeros(image_size_y),
         y=np.arange(image_size_y) + 0.5))  # shift to a pixel center

zoom2_plot_agg_y.add_glyph(zoom2_agg_y_source, Line(x='x', y='y', line_color='steelblue'))


# Histogram zoom2 plot
zoom2_hist_plot = Plot(
    x_range=DataRange1d(),
    y_range=DataRange1d(),
    plot_height=ZOOM_HIST_PLOT_HEIGHT,
    plot_width=sv_zoomplot2.plot.plot_width,
    toolbar_location='left',
)

# ---- tools
zoom2_hist_plot.toolbar.logo = None
# share 'pan', 'box zoom', and 'wheel zoom' with the first histogram plot
zoom2_hist_plot.add_tools(
    zoom1_hist_plot.tools[0], zoom1_hist_plot.tools[1], zoom1_hist_plot.tools[2], ResetTool())

# ---- axes
zoom2_hist_plot.add_layout(LinearAxis(axis_label="Intensity"), place='below')
zoom2_hist_plot.add_layout(
    LinearAxis(axis_label="Counts", major_label_orientation='vertical'), place='right')

# ---- grid lines
zoom2_hist_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
zoom2_hist_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- quad (single bin) glyph
hist2_source = ColumnDataSource(dict(left=[], right=[], top=[]))
zoom2_hist_plot.add_glyph(
    hist2_source, Quad(left="left", right="right", top="top", bottom=0, fill_color="steelblue"))


# Intensity threshold toggle button
def threshold_button_callback(state):
    if state:
        receiver.threshold_flag = True
        threshold_button.button_type = 'primary'
    else:
        receiver.threshold_flag = False
        threshold_button.button_type = 'default'

threshold_button = Toggle(label="Apply Thresholding", active=receiver.threshold_flag)
if receiver.threshold_flag:
    threshold_button.button_type = 'primary'
else:
    threshold_button.button_type = 'default'
threshold_button.on_click(threshold_button_callback)


# Intensity threshold value textinput
def threshold_textinput_callback(_attr, old, new):
    try:
        receiver.threshold = float(new)

    except ValueError:
        threshold_textinput.value = old

threshold_textinput = TextInput(title='Intensity Threshold:', value=str(receiver.threshold))
threshold_textinput.on_change('value', threshold_textinput_callback)


# Aggregation time toggle button
def aggregate_button_callback(state):
    receiver.aggregate_counter = 1  # reset if the button toggled
    aggregate_time_counter_textinput.value = str(receiver.aggregate_counter)
    if state:
        receiver.aggregate_flag = True
        aggregate_button.button_type = 'primary'
    else:
        receiver.aggregate_flag = False
        aggregate_button.button_type = 'default'

aggregate_button = Toggle(label="Apply Aggregation", active=receiver.aggregate_flag)
if receiver.aggregate_flag:
    aggregate_button.button_type = 'primary'
else:
    aggregate_button.button_type = 'default'
aggregate_button.on_click(aggregate_button_callback)


# Aggregation time value textinput
def aggregate_time_textinput_callback(_attr, old, new):
    try:
        new_value = float(new)
        if new_value >= 1:
            receiver.aggregate_time = new_value
        else:
            aggregate_time_textinput.value = old

    except ValueError:
        aggregate_time_textinput.value = old

aggregate_time_textinput = TextInput(
    title='Average Aggregate Time:', value=str(receiver.aggregate_time))
aggregate_time_textinput.on_change('value', aggregate_time_textinput_callback)


# Aggregate time counter value textinput
aggregate_time_counter_textinput = TextInput(
    title='Aggregate Counter:', value=str(receiver.aggregate_counter), disabled=True)


# Saved spectrum lines
zoom1_spectrum_x_source = ColumnDataSource(dict(x=[], y=[]))
zoom1_spectrum_y_source = ColumnDataSource(dict(x=[], y=[]))
zoom2_spectrum_x_source = ColumnDataSource(dict(x=[], y=[]))
zoom2_spectrum_y_source = ColumnDataSource(dict(x=[], y=[]))

zoom1_plot_agg_x.add_glyph(
    zoom1_spectrum_x_source,
    Line(x='x', y='y', line_color='maroon', line_width=2),
)
zoom1_plot_agg_y.add_glyph(
    zoom1_spectrum_y_source,
    Line(x='x', y='y', line_color='maroon', line_width=1),
)
zoom2_plot_agg_x.add_glyph(
    zoom2_spectrum_x_source,
    Line(x='x', y='y', line_color='maroon', line_width=2),
)
zoom2_plot_agg_y.add_glyph(
    zoom2_spectrum_y_source,
    Line(x='x', y='y', line_color='maroon', line_width=1),
)


# Save spectrum button
def save_spectrum_button_callback():
    if current_spectra is not None:
        timenow = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        saved_spectra[timenow] = current_spectra
        save_spectrum_select.options = [*save_spectrum_select.options, timenow]
        save_spectrum_select.value = timenow

save_spectrum_button = Button(label='Save Spectrum')
save_spectrum_button.on_click(save_spectrum_button_callback)


# Saved spectrum select
def save_spectrum_select_callback(_attr, _old, new):
    if new == 'None':
        zoom1_spectrum_x_source.data.update(x=[], y=[])
        zoom1_spectrum_y_source.data.update(x=[], y=[])
        zoom2_spectrum_x_source.data.update(x=[], y=[])
        zoom2_spectrum_y_source.data.update(x=[], y=[])

    else:
        (agg0_1, r0_1, agg1_1, r1_1, agg0_2, r0_2, agg1_2, r1_2) = saved_spectra[new]
        zoom1_spectrum_y_source.data.update(x=agg0_1, y=r0_1)
        zoom1_spectrum_x_source.data.update(x=r1_1, y=agg1_1)
        zoom2_spectrum_y_source.data.update(x=agg0_2, y=r0_2)
        zoom2_spectrum_x_source.data.update(x=r1_2, y=agg1_2)

save_spectrum_select = Select(title='Saved Spectra:', options=['None'], value='None')
save_spectrum_select.on_change('value', save_spectrum_select_callback)


# Histogram controls
# ---- histogram upper range
def hist_upper_callback(_attr, old, new):
    try:
        new_value = float(new)
        if new_value > receiver.hist_lower:
            receiver.hist_upper = new_value
            receiver.force_reset = True
        else:
            hist_upper_textinput.value = old

    except ValueError:
        hist_upper_textinput.value = old

# ---- histogram lower range
def hist_lower_callback(_attr, old, new):
    try:
        new_value = float(new)
        if new_value < receiver.hist_upper:
            receiver.hist_lower = new_value
            receiver.force_reset = True
        else:
            hist_lower_textinput.value = old

    except ValueError:
        hist_lower_textinput.value = old

# ---- histogram number of bins
def hist_nbins_callback(_attr, old, new):
    try:
        new_value = int(new)
        if new_value > 0:
            receiver.hist_nbins = new_value
            receiver.force_reset = True
        else:
            hist_nbins_textinput.value = old

    except ValueError:
        hist_nbins_textinput.value = old

# ---- histogram text imputs
hist_upper_textinput = TextInput(title='Upper Range:', value=str(receiver.hist_upper))
hist_upper_textinput.on_change('value', hist_upper_callback)
hist_lower_textinput = TextInput(title='Lower Range:', value=str(receiver.hist_lower))
hist_lower_textinput.on_change('value', hist_lower_callback)
hist_nbins_textinput = TextInput(title='Number of Bins:', value=str(receiver.hist_nbins))
hist_nbins_textinput.on_change('value', hist_nbins_callback)


# Total intensity plot
total_intensity_plot = Plot(
    title=Title(text="Total Intensity"),
    x_range=DataRange1d(),
    y_range=DataRange1d(),
    plot_height=TOTAL_INT_PLOT_HEIGHT,
    plot_width=TOTAL_INT_PLOT_WIDTH,
)

# ---- tools
total_intensity_plot.add_tools(
    PanTool(), BoxZoomTool(), WheelZoomTool(dimensions='width'), ResetTool())

# ---- axes
total_intensity_plot.add_layout(
    LinearAxis(axis_label="Total intensity", formatter=tick_formatter), place='left')
total_intensity_plot.add_layout(LinearAxis(), place='below')

# ---- grid lines
total_intensity_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
total_intensity_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
total_sum_source = ColumnDataSource(dict(x=[], y=[]))
total_intensity_plot.add_glyph(total_sum_source, Line(x='x', y='y'))


# Zoom1 intensity plot
zoom1_intensity_plot = Plot(
    title=Title(text="Zoom Area 1 Total Intensity"),
    x_range=total_intensity_plot.x_range,
    y_range=DataRange1d(),
    plot_height=TOTAL_INT_PLOT_HEIGHT,
    plot_width=TOTAL_INT_PLOT_WIDTH,
)

# ---- tools
zoom1_intensity_plot.add_tools(
    PanTool(), BoxZoomTool(), WheelZoomTool(dimensions='width'), ResetTool())

# ---- axes
zoom1_intensity_plot.add_layout(
    LinearAxis(axis_label="Intensity", formatter=tick_formatter), place='left')
zoom1_intensity_plot.add_layout(LinearAxis(), place='below')

# ---- grid lines
zoom1_intensity_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
zoom1_intensity_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
zoom1_sum_source = ColumnDataSource(dict(x=[], y=[]))
zoom1_intensity_plot.add_glyph(zoom1_sum_source, Line(x='x', y='y', line_color='red'))


# Zoom2 intensity plot
zoom2_intensity_plot = Plot(
    title=Title(text="Zoom Area 2 Total Intensity"),
    x_range=total_intensity_plot.x_range,
    y_range=DataRange1d(),
    plot_height=TOTAL_INT_PLOT_HEIGHT,
    plot_width=TOTAL_INT_PLOT_WIDTH,
)

# ---- tools
zoom2_intensity_plot.add_tools(
    PanTool(), BoxZoomTool(), WheelZoomTool(dimensions='width'), ResetTool())

# ---- axes
zoom2_intensity_plot.add_layout(
    LinearAxis(axis_label="Intensity", formatter=tick_formatter), place='left')
zoom2_intensity_plot.add_layout(LinearAxis(), place='below')

# ---- grid lines
zoom2_intensity_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
zoom2_intensity_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
zoom2_sum_source = ColumnDataSource(dict(x=[], y=[]))
zoom2_intensity_plot.add_glyph(zoom2_sum_source, Line(x='x', y='y', line_color='green'))


# Intensity stream reset button
def intensity_stream_reset_button_callback():
    global stream_t
    stream_t = 1  # keep the latest point in order to prevent full axis reset
    total_sum_source.data.update(x=[1], y=[total_sum_source.data['y'][-1]])
    zoom1_sum_source.data.update(x=[1], y=[zoom1_sum_source.data['y'][-1]])
    zoom2_sum_source.data.update(x=[1], y=[zoom2_sum_source.data['y'][-1]])

intensity_stream_reset_button = Button(label="Reset", button_type='default')
intensity_stream_reset_button.on_click(intensity_stream_reset_button_callback)


# Stream panel
# ---- connect toggle button
def stream_button_callback(state):
    global connected
    if state:
        connected = True
        stream_button.label = 'Connecting'
        stream_button.button_type = 'default'

    else:
        connected = False
        stream_button.label = 'Connect'
        stream_button.button_type = 'default'


stream_button = Toggle(label="Connect", button_type='default')
stream_button.on_click(stream_button_callback)

# assemble
tab_stream = Panel(child=column(stream_button), title="Stream")


# HDF5 File panel
def hdf5_file_path_update():
    new_menu = []
    if os.path.isdir(hdf5_file_path.value):
        with os.scandir(hdf5_file_path.value) as it:
            for entry in it:
                if entry.is_file() and entry.name.endswith(('.hdf5', '.h5')):
                    new_menu.append((entry.name, entry.name))
    saved_runs_dropdown.menu = sorted(new_menu)

doc.add_periodic_callback(hdf5_file_path_update, HDF5_FILE_PATH_UPDATE_PERIOD)

# ---- folder path text input
def hdf5_file_path_callback(_attr, _old, _new):
    hdf5_file_path_update()

hdf5_file_path = TextInput(title="Folder Path:", value=HDF5_FILE_PATH)
hdf5_file_path.on_change('value', hdf5_file_path_callback)

# ---- saved runs dropdown menu
def saved_runs_dropdown_callback(selection):
    saved_runs_dropdown.label = selection

saved_runs_dropdown = Dropdown(label="Saved Runs", menu=[])
saved_runs_dropdown.on_click(saved_runs_dropdown_callback)

# ---- dataset path text input
hdf5_dataset_path = TextInput(title="Dataset Path:", value=HDF5_DATASET_PATH)

# ---- load button
def mx_image(file, dataset, i):
    # hdf5plugin is required to be loaded prior to h5py without a follow-up use
    import hdf5plugin  # pylint: disable=W0611
    import h5py
    with h5py.File(file, 'r') as f:
        image = f[dataset][i, :, :].astype('float32')
        metadata = dict(shape=list(image.shape))
    return image, metadata

def load_file_button_callback():
    global hdf5_file_data, current_image, current_metadata
    file_name = os.path.join(hdf5_file_path.value, saved_runs_dropdown.label)
    hdf5_file_data = partial(mx_image, file=file_name, dataset=hdf5_dataset_path.value)
    current_image, current_metadata = hdf5_file_data(i=hdf5_pulse_slider.value)
    update_client(current_image, current_metadata, (None, None, None))

load_file_button = Button(label="Load", button_type='default')
load_file_button.on_click(load_file_button_callback)

# ---- pulse number slider
def hdf5_pulse_slider_callback(_attr, _old, new):
    global hdf5_file_data, current_image, current_metadata
    current_image, current_metadata = hdf5_file_data(i=new['value'][0])
    update_client(current_image, current_metadata, (None, None, None))

hdf5_pulse_slider_source = ColumnDataSource(dict(value=[]))
hdf5_pulse_slider_source.on_change('data', hdf5_pulse_slider_callback)

hdf5_pulse_slider = Slider(
    start=0, end=99, value=0, step=1, title="Pulse Number", callback_policy='mouseup')

hdf5_pulse_slider.callback = CustomJS(
    args=dict(source=hdf5_pulse_slider_source),
    code="""source.data = {value: [cb_obj.value]}""")

# assemble
tab_hdf5file = Panel(
    child=column(
        hdf5_file_path, saved_runs_dropdown, hdf5_dataset_path, load_file_button,
        hdf5_pulse_slider),
    title="HDF5 File")

data_source_tabs = Tabs(tabs=[tab_stream, tab_hdf5file])


# Colormapper panel
colormap_panel = column(
    sv_colormapper.select,
    Spacer(height=10),
    sv_colormapper.scale_radiobuttongroup,
    Spacer(height=10),
    sv_colormapper.auto_toggle,
    sv_colormapper.display_max_textinput,
    sv_colormapper.display_min_textinput,
)


# Metadata datatable
sv_metadata = sv.MetadataHandler(
    datatable_height=420, datatable_width=800, check_shape=(IMAGE_SIZE_Y, IMAGE_SIZE_X),
)


# Final layouts
layout_main = column(sv_mainplot.plot)

layout_zoom1 = column(
    zoom1_plot_agg_x,
    row(sv_zoomplot1.plot, zoom1_plot_agg_y),
    row(Spacer(), zoom1_hist_plot, Spacer()))

layout_zoom2 = column(
    zoom2_plot_agg_x,
    row(sv_zoomplot2.plot, zoom2_plot_agg_y),
    row(Spacer(), zoom2_hist_plot, Spacer()))

layout_thr_agg = row(
    column(threshold_button, threshold_textinput),
    Spacer(width=30),
    column(aggregate_button, aggregate_time_textinput, aggregate_time_counter_textinput))

layout_spectra = column(save_spectrum_button, save_spectrum_select)

layout_hist_controls = column(hist_upper_textinput, hist_lower_textinput, hist_nbins_textinput)

layout_utility = column(
    gridplot([total_intensity_plot, zoom1_intensity_plot, zoom2_intensity_plot],
             ncols=1, toolbar_location='left', toolbar_options=dict(logo=None)),
    row(Spacer(width=850), intensity_stream_reset_button))

layout_controls = column(colormap_panel, data_source_tabs)

layout_metadata = column(
    sv_metadata.datatable,
    row(sv_metadata.show_all_toggle, sv_metadata.issues_dropdown),
)

final_layout = column(
    layout_main,
    Spacer(),
    row(layout_zoom1, Spacer(), layout_zoom2, Spacer(),
        column(layout_utility, Spacer(height=10),
               row(layout_controls, Spacer(width=50), layout_metadata))),
    row(column(Spacer(height=20), layout_thr_agg), Spacer(width=150),
        column(Spacer(height=20), layout_spectra), Spacer(width=200),
        layout_hist_controls))

doc.add_root(row(Spacer(width=20), final_layout))


@gen.coroutine
def update_client(image, metadata, stats):
    global stream_t, current_spectra

    sv_colormapper.update(image)

    pil_im = PIL_Image.fromarray(image)

    sv_mainplot.update(image, pil_im)
    sv_zoomplot1.update(image, pil_im)
    sv_zoomplot2.update(image, pil_im)

    # update ranges for statistics calculation
    receiver.zoom1_y_start = sv_zoomplot1.y_start
    receiver.zoom1_y_end = sv_zoomplot1.y_end
    receiver.zoom1_x_start = sv_zoomplot1.x_start
    receiver.zoom1_x_end = sv_zoomplot1.x_end

    receiver.zoom2_y_start = sv_zoomplot2.y_start
    receiver.zoom2_y_end = sv_zoomplot2.y_end
    receiver.zoom2_x_start = sv_zoomplot2.x_start
    receiver.zoom2_x_end = sv_zoomplot2.x_end

    # Statistics
    zoom1_counts, zoom2_counts, edges = stats

    y_start = int(np.floor(sv_zoomplot1.y_start))
    y_end = int(np.ceil(sv_zoomplot1.y_end))
    x_start = int(np.floor(sv_zoomplot1.x_start))
    x_end = int(np.ceil(sv_zoomplot1.x_end))

    im_block = image[y_start:y_end, x_start:x_end]

    zoom1_agg_y = np.sum(im_block, axis=1)
    zoom1_agg_x = np.sum(im_block, axis=0)
    zoom1_r_y = np.arange(y_start, y_end) + 0.5
    zoom1_r_x = np.arange(x_start, x_end) + 0.5

    total_sum_zoom1 = np.sum(im_block)
    if edges is not None:
        hist1_source.data.update(left=edges[:-1], right=edges[1:], top=zoom1_counts)
    zoom1_agg_y_source.data.update(x=zoom1_agg_y, y=zoom1_r_y)
    zoom1_agg_x_source.data.update(x=zoom1_r_x, y=zoom1_agg_x)

    y_start = int(np.floor(sv_zoomplot2.y_start))
    y_end = int(np.ceil(sv_zoomplot2.y_end))
    x_start = int(np.floor(sv_zoomplot2.x_start))
    x_end = int(np.ceil(sv_zoomplot2.x_end))

    im_block = image[y_start:y_end, x_start:x_end]

    zoom2_agg_y = np.sum(im_block, axis=1)
    zoom2_agg_x = np.sum(im_block, axis=0)
    zoom2_r_y = np.arange(y_start, y_end) + 0.5
    zoom2_r_x = np.arange(x_start, x_end) + 0.5

    total_sum_zoom2 = np.sum(im_block)
    if edges is not None:
        hist2_source.data.update(left=edges[:-1], right=edges[1:], top=zoom2_counts)
    zoom2_agg_y_source.data.update(x=zoom2_agg_y, y=zoom2_r_y)
    zoom2_agg_x_source.data.update(x=zoom2_r_x, y=zoom2_agg_x)

    if connected and receiver.state == 'receiving':
        stream_t += 1
        total_sum_source.stream(
            new_data=dict(x=[stream_t], y=[np.sum(image, dtype=np.float)]),
            rollover=STREAM_ROLLOVER)
        zoom1_sum_source.stream(
            new_data=dict(x=[stream_t], y=[total_sum_zoom1]), rollover=STREAM_ROLLOVER)
        zoom2_sum_source.stream(
            new_data=dict(x=[stream_t], y=[total_sum_zoom2]), rollover=STREAM_ROLLOVER)


    # Save spectrum
    current_spectra = (zoom1_agg_y, zoom1_r_y, zoom1_agg_x, zoom1_r_x,
                       zoom2_agg_y, zoom2_r_y, zoom2_agg_x, zoom2_r_x)

    # Parse metadata
    metadata_toshow = sv_metadata.parse(metadata)
    sv_metadata.update(metadata_toshow)


@gen.coroutine
def internal_periodic_callback():
    global current_image, current_metadata, current_stats
    if sv_mainplot.plot.inner_width is None:
        # wait for the initialization to finish, thus skip this periodic callback
        return

    if connected:
        if receiver.state == 'polling':
            stream_button.label = 'Polling'
            stream_button.button_type = 'warning'

        elif receiver.state == 'receiving':
            stream_button.label = 'Receiving'
            stream_button.button_type = 'success'

            # capture current values
            current_image = receiver.current_image
            current_stats = receiver.current_stats
            current_metadata = receiver.current_metadata

            aggregate_time_counter_textinput.value = str(receiver.aggregate_counter)

    if current_image.shape != (1, 1):
        doc.add_next_tick_callback(
            partial(update_client, image=current_image, metadata=current_metadata,
                    stats=current_stats))

doc.add_periodic_callback(internal_periodic_callback, 1000 / APP_FPS)