EDIT_MODELS = "nano-banana-2-edit,seedream-v5-pro-edit,muse-image-edit"
REMAKE_MODELS = ("kling-v3-standard-image-to-video,wan-2-7-image-to-video,kling-v3-pro-image-to-video,"
                 "veo3-1-fast-image-to-video")
KEY_UNKNOWN_MICRO = 100_000


def generate_keys(*args, **kwargs):
    raise NotImplementedError


def generate_remake(*args, **kwargs):
    raise NotImplementedError
