import os
from datetime import datetime
from functools import partial

import numpy as np
from bokeh.io import curdoc
from bokeh.layouts import column, gridplot, row
from bokeh.models import BasicTicker, BasicTickFormatter, BoxZoomTool, Button, ColumnDataSource, \
    CustomJS, DataRange1d, DatetimeAxis, Dropdown, Grid, Line, LinearAxis, Panel, PanTool, Plot, \
    Rect, ResetTool, Slider, Spacer, Tabs, TextInput, Toggle, WheelZoomTool
from PIL import Image as PIL_Image
from tornado import gen

import receiver
import streamvis as sv

doc = curdoc()
doc.title = receiver.args.page_title

# initial image size to organize placeholders for actual data
image_size_x = 100
image_size_y = 100

current_image = np.zeros((1, 1), dtype='float32')
current_metadata = dict(shape=[image_size_y, image_size_x])

connected = False

# Currently, it's possible to control only a canvas size, but not a size of the plotting area.
MAIN_CANVAS_WIDTH = 800 + 54
MAIN_CANVAS_HEIGHT = 800 + 94

ZOOM_CANVAS_WIDTH = 600 + 54
ZOOM_CANVAS_HEIGHT = 600 + 29

DEBUG_INTENSITY_WIDTH = 700

APP_FPS = 1
STREAM_ROLLOVER = 36000

HDF5_FILE_PATH = '/filepath'
HDF5_FILE_PATH_UPDATE_PERIOD = 10000  # ms
HDF5_DATASET_PATH = '/entry/data/data'
hdf5_file_data = lambda pulse: None

agg_plot_size = 200

ZOOM_INIT_WIDTH = image_size_x
ZOOM_INIT_HEIGHT = image_size_y
ZOOM1_INIT_X = 0

# Custom tick formatter for displaying large numbers
tick_formatter = BasicTickFormatter(precision=1)

# Create colormapper
sv_colormapper = sv.ColorMapper()


# Main plot
sv_mainplot = sv.ImagePlot(sv_colormapper)

# ---- add colorbar
sv_colormapper.color_bar.width = MAIN_CANVAS_WIDTH // 2
sv_colormapper.color_bar.location = (0, -5)
sv_mainplot.plot.add_layout(sv_colormapper.color_bar, place='below')


# Zoom plot
sv_zoomplot = sv.ImagePlot(
    sv_colormapper,
    plot_height=ZOOM_CANVAS_HEIGHT, plot_width=ZOOM_CANVAS_WIDTH,
)

# ---- add rectangle glyph of zoom area to the main plot
zoom1_area_source = ColumnDataSource(
    dict(x=[ZOOM1_INIT_X + ZOOM_INIT_WIDTH / 2], y=[ZOOM_INIT_HEIGHT / 2],
         width=[ZOOM_INIT_WIDTH], height=[image_size_y]))

rect_red = Rect(
    x='x', y='y', width='width', height='height', line_color='red', line_width=2, fill_alpha=0)
sv_mainplot.plot.add_glyph(zoom1_area_source, rect_red)

jscode_move_rect = """
    var data = source.data;
    var start = cb_obj.start;
    var end = cb_obj.end;
    data['%s'] = [start + (end - start) / 2];
    data['%s'] = [end - start];
    source.change.emit();
"""

sv_zoomplot.plot.x_range.callback = CustomJS(
    args=dict(source=zoom1_area_source), code=jscode_move_rect % ('x', 'width'))

sv_zoomplot.plot.y_range.callback = CustomJS(
    args=dict(source=zoom1_area_source), code=jscode_move_rect % ('y', 'height'))


# Aggregate zoom1 plot along x axis
zoom1_plot_agg_x = Plot(
    x_range=sv_zoomplot.plot.x_range,
    y_range=DataRange1d(),
    plot_height=agg_plot_size,
    plot_width=sv_zoomplot.plot.plot_width,
    toolbar_location=None,
)

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

zoom1_plot_agg_x.add_glyph(zoom1_agg_x_source, Line(x='x', y='y', line_color='steelblue'))


# Aggregate zoom1 plot along y axis
zoom1_plot_agg_y = Plot(
    x_range=DataRange1d(),
    y_range=sv_zoomplot.plot.y_range,
    plot_height=sv_zoomplot.plot.plot_height,
    plot_width=agg_plot_size,
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


# Histogram plot
sv_hist = sv.Histogram(nplots=1, plot_height=400, plot_width=700)


# Total intensity plot
total_intensity_plot = Plot(
    x_range=DataRange1d(),
    y_range=DataRange1d(),
    plot_height=agg_plot_size,
    plot_width=DEBUG_INTENSITY_WIDTH,
)

# ---- tools
total_intensity_plot.add_tools(
    PanTool(), BoxZoomTool(), WheelZoomTool(dimensions='width'), ResetTool())

# ---- axes
total_intensity_plot.add_layout(
    LinearAxis(axis_label="Image intensity", formatter=tick_formatter), place='left')
total_intensity_plot.add_layout(DatetimeAxis(major_label_text_font_size='0pt'), place='below')

# ---- grid lines
total_intensity_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
total_intensity_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
total_sum_source = ColumnDataSource(dict(x=[], y=[]))
total_intensity_plot.add_glyph(total_sum_source, Line(x='x', y='y'))


# Zoom1 intensity plot
zoom1_intensity_plot = Plot(
    x_range=total_intensity_plot.x_range,
    y_range=DataRange1d(),
    plot_height=agg_plot_size,
    plot_width=DEBUG_INTENSITY_WIDTH,
)

# ---- tools
zoom1_intensity_plot.add_tools(
    PanTool(), BoxZoomTool(), WheelZoomTool(dimensions='width'), ResetTool())

# ---- axes
zoom1_intensity_plot.add_layout(
    LinearAxis(axis_label="Zoom Intensity", formatter=tick_formatter), place='left')
zoom1_intensity_plot.add_layout(DatetimeAxis(), place='below')

# ---- grid lines
zoom1_intensity_plot.add_layout(Grid(dimension=0, ticker=BasicTicker()))
zoom1_intensity_plot.add_layout(Grid(dimension=1, ticker=BasicTicker()))

# ---- line glyph
zoom1_sum_source = ColumnDataSource(dict(x=[], y=[]))
zoom1_intensity_plot.add_glyph(zoom1_sum_source, Line(x='x', y='y', line_color='red'))


# Intensity stream reset button
def intensity_stream_reset_button_callback():
    stream_t = datetime.now()  # keep the latest point in order to prevent full axis reset
    total_sum_source.data.update(x=[stream_t], y=[total_sum_source.data['y'][-1]])
    zoom1_sum_source.data.update(x=[stream_t], y=[zoom1_sum_source.data['y'][-1]])

intensity_stream_reset_button = Button(label="Reset", button_type='default')
intensity_stream_reset_button.on_click(intensity_stream_reset_button_callback)


# Stream panel
# ---- image buffer slider
def image_buffer_slider_callback(_attr, _old, new):
    md, image = receiver.data_buffer[round(new['value'][0])]
    doc.add_next_tick_callback(partial(update_client, image=image, metadata=md))

image_buffer_slider_source = ColumnDataSource(dict(value=[]))
image_buffer_slider_source.on_change('data', image_buffer_slider_callback)

image_buffer_slider = Slider(
    start=0, end=1, value=0, step=1, title="Buffered Image", callback_policy='mouseup')

image_buffer_slider.callback = CustomJS(
    args=dict(source=image_buffer_slider_source),
    code="""source.data = {value: [cb_obj.value]}""")

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
tab_stream = Panel(child=column(image_buffer_slider, stream_button), title="Stream")


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
    update_client(current_image, current_metadata)

load_file_button = Button(label="Load", button_type='default')
load_file_button.on_click(load_file_button_callback)

# ---- pulse number slider
def hdf5_pulse_slider_callback(_attr, _old, new):
    global hdf5_file_data, current_image, current_metadata
    current_image, current_metadata = hdf5_file_data(i=new['value'][0])
    update_client(current_image, current_metadata)

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

aggregate_time_textinput = TextInput(title='Aggregate Time:', value=str(receiver.aggregate_time))
aggregate_time_textinput.on_change('value', aggregate_time_textinput_callback)


# Aggregate time counter value textinput
aggregate_time_counter_textinput = TextInput(
    title='Aggregate Counter:', value=str(receiver.aggregate_counter), disabled=True)


# Metadata datatable
sv_metadata = sv.MetadataHandler()


# Final layouts
layout_main = column(sv_mainplot.plot)

layout_zoom = column(
    zoom1_plot_agg_x,
    row(sv_zoomplot.plot, zoom1_plot_agg_y))

layout_utility = column(
    gridplot([total_intensity_plot, zoom1_intensity_plot],
             ncols=1, toolbar_location='left', toolbar_options=dict(logo=None)),
    intensity_stream_reset_button)

layout_controls = column(colormap_panel, data_source_tabs)

layout_threshold_aggr = column(
    threshold_button, threshold_textinput,
    Spacer(height=30),
    aggregate_button, aggregate_time_textinput, aggregate_time_counter_textinput)

layout_metadata = column(
    sv_metadata.datatable,
    row(sv_metadata.show_all_toggle, sv_metadata.issues_dropdown),
)

final_layout = column(
    row(layout_main, layout_controls, column(layout_metadata, layout_utility)),
    row(layout_zoom, layout_threshold_aggr, sv_hist.plots[0]))

doc.add_root(final_layout)


@gen.coroutine
def update_client(image, metadata):
    sv_colormapper.update(image)

    pil_im = PIL_Image.fromarray(image)

    sv_mainplot.update(image, pil_im)
    sv_zoomplot.update(image, pil_im)

    # Statistics
    y_start = int(np.floor(sv_zoomplot.y_start))
    y_end = int(np.ceil(sv_zoomplot.y_end))
    x_start = int(np.floor(sv_zoomplot.x_start))
    x_end = int(np.ceil(sv_zoomplot.x_end))

    im_block = image[y_start:y_end, x_start:x_end]

    agg_y = np.mean(im_block, axis=1)
    agg_x = np.mean(im_block, axis=0)
    r_y = np.arange(y_start, y_end) + 0.5
    r_x = np.arange(x_start, x_end) + 0.5

    # Update histogram
    sv_hist.update([im_block])

    zoom1_agg_y_source.data.update(x=agg_y, y=r_y)
    zoom1_agg_x_source.data.update(x=r_x, y=agg_x)

    stream_t = datetime.now()
    total_sum = np.sum(im_block)
    zoom1_sum_source.stream(new_data=dict(x=[stream_t], y=[total_sum]), rollover=STREAM_ROLLOVER)
    total_sum_source.stream(
        new_data=dict(x=[stream_t], y=[np.sum(image, dtype=np.float)]), rollover=STREAM_ROLLOVER)

    # Parse and update metadata
    metadata_toshow = sv_metadata.parse(metadata)
    sv_metadata.update(metadata_toshow)


@gen.coroutine
def internal_periodic_callback():
    global current_image, current_metadata
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

            # Set slider to the right-most position
            if len(receiver.data_buffer) > 1:
                image_buffer_slider.end = len(receiver.data_buffer) - 1
                image_buffer_slider.value = len(receiver.data_buffer) - 1

            current_metadata, current_image = receiver.data_buffer[-1]
            current_image = current_image.copy()

            aggregate_time_counter_textinput.value = str(receiver.aggregate_counter)

    if current_image.shape != (1, 1):
        doc.add_next_tick_callback(partial(
            update_client, image=current_image, metadata=current_metadata))

doc.add_periodic_callback(internal_periodic_callback, 1000 / APP_FPS)