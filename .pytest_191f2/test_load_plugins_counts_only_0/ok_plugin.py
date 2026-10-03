from core.plugins import InpaintEnginePlugin, register_engine
class _P(InpaintEnginePlugin):
    name = 'ok_engine'
    display_name = '插件引擎'
    def inpaint(self, image, mask, *, device=None, progress=None):
        return image
register_engine(_P)
