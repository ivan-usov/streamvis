import numpy as np
from bokeh.models import CheckboxGroup, Spinner, TextInput


class ImageProcessor:
    def __init__(self):
        """Initialize an image processor.
        """
        self.aggregated_image = np.zeros((1, 1), dtype=np.float32)

        # Intensity threshold toggle
        threshold_toggle = CheckboxGroup(labels=["Apply Thresholding"], default_size=145)
        self.threshold_toggle = threshold_toggle

        # Threshold min/max value spinners
        self.threshold_min_spinner = Spinner(
            title="Min Intensity:", value=0, step=0.1, default_size=145
        )
        self.threshold_max_spinner = Spinner(
            title="Max Intensity:", value=1000, step=0.1, default_size=145
        )

        # Aggregation time toggle
        aggregate_toggle = CheckboxGroup(labels=["Apply Aggregation"], default_size=145)
        self.aggregate_toggle = aggregate_toggle

        # Aggregate time spinner
        self.aggregate_time_spinner = Spinner(
            title="Aggregate Time:", value=0, low=0, step=1, default_size=145
        )

        # Aggregate time counter textinput
        aggregate_time_counter_textinput = TextInput(
            title="Time Counter:", value=str(1), disabled=True, default_size=145
        )
        self.aggregate_time_counter_textinput = aggregate_time_counter_textinput

        # Show Average toggle
        average_toggle = CheckboxGroup(labels=["Show Average"], default_size=145)
        self.average_toggle = average_toggle

    @property
    def threshold_min(self):
        """Minimal image threshold value (readonly).
        """
        return self.threshold_min_spinner.value

    @property
    def threshold_max(self):
        """Maximal image threshold value (readonly).
        """
        return self.threshold_max_spinner.value

    @property
    def aggregate_time(self):
        """A number of image aggregation before resetting (readonly).
        """
        return self.aggregate_time_spinner.value

    @property
    def aggregate_counter(self):
        """A current number of aggregated images (readonly).
        """
        return int(self.aggregate_time_counter_textinput.value)

    @aggregate_counter.setter
    def aggregate_counter(self, value):
        self.aggregate_time_counter_textinput.value = str(value)

    def update(self, metadata, image):
        """Trigger an update for the image processor.

        Args:
            image (ndarray): Input image to be processed.

        Returns:
            (ndarray, ndarray, bool): Resulting thresholding image, aggregated image and reset flag.
        """
        if image.shape == (1, 1):
            # skip update if the image is dummy
            return np.zeros((1, 1), dtype="float32"), np.zeros((1, 1), dtype="float32"), False

        counts = metadata.get("aggregated_images", 1)

        thr_image = image.copy()
        if self.threshold_toggle.active:
            ind = (thr_image < self.threshold_min) | (self.threshold_max < thr_image)
            thr_image[ind] = 0

        if (
            self.aggregate_toggle.active
            and (self.aggregate_time == 0 or self.aggregate_time > self.aggregate_counter)
            and self.aggregated_image.shape == image.shape
        ):
            self.aggregated_image += thr_image
            self.aggregate_counter += counts
            reset = False
        else:
            self.aggregated_image = thr_image
            self.aggregate_counter = counts
            reset = True

        if self.average_toggle.active:
            aggregated_image = self.aggregated_image / self.aggregate_counter
        else:
            aggregated_image = self.aggregated_image

        return thr_image, aggregated_image, reset
