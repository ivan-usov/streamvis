import logging

import numpy as np
from bokeh.models import ColumnDataSource, ImageRGBA, Toggle

placeholder = np.ones((1, 1, 4), dtype='uint8')

logger = logging.getLogger(__name__)


class Mask:
    def __init__(self, image_views):
        import streamvis as sv
        self.receiver = sv.current_receiver

        self.current_file = ''
        self.mask = None

        # ---- rgba image glyph
        self._source = ColumnDataSource(dict(image=[placeholder], x=[0], y=[0], dw=[1], dh=[1]))

        rgba_glyph = ImageRGBA(image='image', x='x', y='y', dw='dw', dh='dh', global_alpha=0)

        for image_view in image_views:
            image_renderer = image_view.plot.add_glyph(self._source, rgba_glyph)
            image_renderer.view.source = ColumnDataSource()

        # ---- toggle button
        def toggle_callback(state):
            if state:
                rgba_glyph.global_alpha = 1
            else:
                rgba_glyph.global_alpha = 0

        toggle = Toggle(label="Show Mask", button_type='default')
        toggle.on_click(toggle_callback)
        self.toggle = toggle

    def update(self, sv_metadata):
        receiver = self.receiver
        if receiver.pedestal_file and receiver.jf_handler.pixel_mask is not None:
            if self.current_file != receiver.pedestal_file:
                mm = receiver.jf_handler.module_map
                receiver.jf_handler.module_map = None
                mask_data = ~receiver.jf_handler.apply_geometry(~receiver.jf_handler.pixel_mask)
                receiver.jf_handler.module_map = mm

                mask = np.zeros((*mask_data.shape, 4), dtype='uint8')
                mask[:, :, 1] = 255
                mask[:, :, 3] = 255 * mask_data

                self.mask = mask
                self._source.data.update(image=[mask], dh=[mask.shape[0]], dw=[mask.shape[1]])

        else:
            self.mask = None
            self._source.data.update(image=[placeholder])

        self.current_file = receiver.pedestal_file

        if self.toggle.active and self.mask is None:
            sv_metadata.add_issue('No pedestal file has been provided')
