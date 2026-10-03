from core.plugins import InpaintEnginePlugin, register_engine
class _P(InpaintEnginePlugin):
    name = 'dup_engine'
    display_name = '第二个'
    def inpaint(self, image, mask, *, device=None, progress=None):
        return image
register_engine(_P)
