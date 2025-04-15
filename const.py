from enum import Enum, IntEnum

class PlEvents(Enum):
    GAZE_DATA = "gaze_data"
    
class ScrcpyEvents(Enum):
    INIT = "init"
    FRAME = "frame"
    DISCONNECT = "disconnect"
    PACKET = "packet"

class ScrcpyControls(IntEnum):
    GET_CURRENT_TIME = 18

class ScrcpyMasks(IntEnum):
    PACKET_PTS_MASK = (1 << 62) - 1