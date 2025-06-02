"""Device capabilities for the U-tec library."""

from typing import List, Dict, Type
from ..abstract import BaseDeviceCapabilities


class DeviceDefinition(BaseDeviceCapabilities):
    """Base class for device capabilities."""
    
    model = ""

    def __init__(self) -> None:
        """Initialize device capabilities."""
        self.lock: bool = False
        self.door: bool = False
        self.keypad: bool = False
        self.fingprinter: bool = False
        self.doublefp: bool = False
        self.bluetooth: bool = False
        self.rfid: bool = False
        self.rfid_once: bool = False
        self.rfid_twice: bool = False
        self.autobolt: bool = False
        self.autolock: bool = False
        self.autounlock: bool = False
        self.direction: bool = False
        self.update_ota: bool = False
        self.update_oad: bool = False
        self.update_wifi: bool = False
        self.alerts: bool = False
        self.mutemode: bool = False
        self.passage: bool = False
        self.lockout: bool = False
        self.manual: bool = False
        self.shakeopen: bool = False
        self.moreadmin: bool = False
        self.morepwd: bool = False
        self.timelimit: bool = False
        self.morelanguage: bool = False
        self.needregristerpwd: bool = False
        self.locklocal: bool = False
        self.havesn: bool = False
        self.clone: bool = False
        self.customuserid: bool = False
        self.bt264: bool = False
        self.keepalive: bool = False
        self.passageautolock: bool = False
        self.doorsensor: bool = False
        self.zwave: bool = False
        self.needreadmodel: bool = False
        self.needsycbuser: bool = False
        self.bt_close: bool = False
        self.singlelatchboltmortic: bool = False
        self.smartphone_nfc: bool = False
        self.update_2642: bool = False
        self.isautodirection: bool = False
        self.ishomekit: bool = False
        self.isyeeuu: bool = False
        self.secondsarray = []
        self.mtimearray = []
        self.adduserremovenum = 4
        
    @classmethod
    def get_supported_features(cls) -> List[str]:
        """Get a list of supported features for this device model.
        
        Returns:
            List of supported features.
        """
        # This is a simple implementation that returns all features set to True
        obj = cls()
        return [attr for attr, value in vars(obj).items()
                if isinstance(value, bool) and value]


class DeviceLockLatch5Finger(DeviceDefinition):
    """Latch-5-F device capabilities."""
    
    model = "Latch-5-F"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()

        self.bluetooth = True
        self.autolock = True
        self.update_wifi = True
        self.alerts = True
        self.mutemode = True
        self.doublefp = True
        self.keypad = True
        self.fingprinter = True
        self.needregristerpwd = True
        self.havesn = True
        self.moreadmin = True
        self.timelimit = True
        self.passage = True
        self.lockout = True
        self.bt264 = True
        self.keepalive = True
        self.passageautolock = True
        self.singlelatchboltmortic = True
        self.smartphone_nfc = True
        self.bt_close = True


class DeviceLockLatch5NFC(DeviceDefinition):
    """Latch-5-NFC device capabilities."""
    
    model = "Latch-5-NFC"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()

        self.bluetooth = True
        self.autolock = True
        self.update_wifi = True
        self.alerts = True
        self.mutemode = True
        self.rfid = True
        self.rfid_twice = True
        self.keypad = True
        self.needregristerpwd = True
        self.havesn = True
        self.moreadmin = True
        self.timelimit = True
        self.passage = True
        self.lockout = True
        self.bt264 = True
        self.keepalive = True
        self.passageautolock = True
        self.singlelatchboltmortic = True
        self.smartphone_nfc = True
        self.bt_close = True


class DeviceLockUL1(DeviceDefinition):
    """UL1-BT device capabilities."""
    
    model = "UL1-BT"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()

        self.bluetooth = True
        self.rfid = True
        self.rfid_twice = True
        self.fingprinter = True
        self.autobolt = True
        self.update_ota = True
        self.update_oad = True
        self.alerts = True
        self.shakeopen = True
        self.mutemode = True
        self.passage = True
        self.lockout = True
        self.havesn = True
        self.direction = True
        self.keepalive = True
        self.singlelatchboltmortic = True


class DeviceLockBoltNFC(DeviceDefinition):
    """Bolt-NFC device capabilities."""
    
    model = "Bolt-NFC"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()

        self.lock = True
        self.bluetooth = True
        self.autolock = True
        self.update_ota = True
        self.update_wifi = True
        self.direction = True
        self.alerts = True
        self.mutemode = True
        self.manual = True
        self.shakeopen = True
        self.havesn = True
        self.rfid = True
        self.keypad = True
        self.needregristerpwd = True
        self.timelimit = True
        self.moreadmin = True
        self.lockout = True
        self.bt264 = True
        self.doorsensor = True
        self.keepalive = True
        self.autounlock = True
        self.smartphone_nfc = True
        self.update_2642 = True
        self.isautodirection = True
        self.ishomekit = True


class DeviceLockLever(DeviceDefinition):
    """LEVER device capabilities."""
    
    model = "LEVER"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()
        self.bluetooth = True
        self.autolock = True
        self.update_ota = True
        self.alerts = True
        self.mutemode = True
        self.shakeopen = True
        self.fingprinter = True
        self.keypad = True
        self.doublefp = True
        self.needregristerpwd = True
        self.havesn = True
        self.moreadmin = True
        self.timelimit = True
        self.passage = True
        self.lockout = True
        self.bt264 = True
        self.keepalive = True
        self.passageautolock = True
        self.singlelatchboltmortic = True


class DeviceLockUBolt(DeviceDefinition):
    """U-Bolt device capabilities."""
    
    model = "U-Bolt"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()
        self.lock = True
        self.bluetooth = True
        self.autolock = True
        self.autounlock = True
        self.update_ota = True
        self.direction = True
        self.alerts = True
        self.mutemode = True
        self.manual = True
        self.shakeopen = True
        self.havesn = True
        self.moreadmin = True
        self.needreadmodel = True
        self.keypad = True
        self.lockout = True
        self.timelimit = True
        self.needregristerpwd = True
        self.bt264 = True
        self.keepalive = True


class DeviceLockUboltWiFi(DeviceDefinition):
    """U-Bolt-WiFi device capabilities."""
    
    model = "U-Bolt-WiFi"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()
        self.lock = True
        self.bluetooth = True
        self.autolock = True
        self.update_ota = True
        self.update_wifi = True
        self.direction = True
        self.alerts = True
        self.mutemode = True
        self.manual = True
        self.shakeopen = True
        self.havesn = True
        self.needreadmodel = True
        self.keypad = True
        self.needregristerpwd = True
        self.timelimit = True
        self.moreadmin = True
        self.lockout = True
        self.bt264 = True
        self.doorsensor = True
        self.keepalive = True
        self.autounlock = True


class DeviceLockUBoltZwave(DeviceDefinition):
    """U-Bolt-ZWave device capabilities."""
    
    model = "U-Bolt-ZWave"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()
        self.lock = True
        self.bluetooth = True
        self.autolock = True
        self.update_ota = True
        self.direction = True
        self.alerts = True
        self.mutemode = True
        self.manual = True
        self.shakeopen = True
        self.havesn = True
        self.needreadmodel = True
        self.keypad = True
        self.needregristerpwd = True
        self.timelimit = True
        self.moreadmin = True
        self.lockout = True
        self.bt264 = True
        self.doorsensor = True
        self.keepalive = True
        self.autounlock = True
        self.zwave = True


class DeviceLockUL3(DeviceDefinition):
    """SmartLockByBle device capabilities."""
    
    model = "SmartLockByBle"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()
        self.bluetooth = True
        self.keypad = True
        self.fingprinter = True
        self.shakeopen = True
        self.morepwd = True
        self.passage = True
        self.lockout = True
        self.locklocal = True
        self.needsycbuser = True
        self.clone = True
        self.customuserid = True
        self.singlelatchboltmortic = True
        self.keepalive = True


class DeviceLockUL32ND(DeviceDefinition):
    """UL3-2ND device capabilities."""
    
    model = "UL3-2ND"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()
        self.bluetooth = True
        self.autolock = True
        self.update_ota = True
        self.alerts = True
        self.mutemode = True
        self.shakeopen = True
        self.fingprinter = True
        self.keypad = True
        self.doublefp = True
        self.needregristerpwd = True
        self.havesn = True
        self.locklocal = True
        self.needsycbuser = True
        self.moreadmin = True
        self.customuserid = True
        self.timelimit = True
        self.passage = True
        self.lockout = True
        self.bt264 = True
        self.keepalive = True
        self.passageautolock = True
        self.singlelatchboltmortic = True


class DeviceLockUL300(DeviceDefinition):
    """UL300 device capabilities."""
    
    model = "UL300"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()
        self.bluetooth = True
        self.rfid = True
        self.rfid_once = True
        self.keypad = True
        self.fingprinter = True
        self.update_ota = True
        self.update_oad = True
        self.alerts = True
        self.shakeopen = True
        self.mutemode = True
        self.moreadmin = True
        self.timelimit = True
        self.passage = True
        self.lockout = True
        self.morelanguage = True
        self.locklocal = True
        self.needsycbuser = True
        self.havesn = True
        self.keepalive = True
        self.singlelatchboltmortic = True
        self.adduserremovenum = 5


class DeviceLockUBoltPro(DeviceDefinition):
    """U-Bolt-PRO device capabilities."""
    
    model = "U-Bolt-PRO"

    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()
        self.lock = True
        self.bluetooth = True
        self.autolock = True
        self.autounlock = True
        self.update_ota = True
        self.update_wifi = True
        self.direction = True
        self.alerts = True
        self.mutemode = True
        self.manual = True
        self.shakeopen = True
        self.havesn = True
        self.moreadmin = True
        self.keypad = True
        self.lockout = True
        self.timelimit = True
        self.needregristerpwd = True
        self.bt264 = True
        self.doorsensor = True
        self.keepalive = True
        self.bt2640ByJoe = True  
        self.needreadmodel = False  # ← Pro models don't need model reading


class GenericLock(DeviceDefinition):
    """Generic lock device capabilities."""
    
    model = "Generic"
    
    def __init__(self) -> None:
        """Initialize device capabilities."""
        super().__init__()

        self.bluetooth = False
        self.autolock = True
        self.mutemode = True
        self.havesn = True
        self.timelimit = True
        self.passage = True
        self.lockout = True
        self.bt264 = True
        self.keepalive = True
        self.bt_close = True


# Dictionary of known device models
known_devices: Dict[str, DeviceDefinition] = {
    DeviceLockLatch5Finger.model: DeviceLockLatch5Finger(),
    DeviceLockLatch5NFC.model: DeviceLockLatch5NFC(),
    DeviceLockUL1.model: DeviceLockUL1(),
    DeviceLockBoltNFC.model: DeviceLockBoltNFC(),
    DeviceLockLever.model: DeviceLockLever(),
    DeviceLockUBolt.model: DeviceLockUBolt(),
    DeviceLockUboltWiFi.model: DeviceLockUboltWiFi(),
    DeviceLockUBoltZwave.model: DeviceLockUBoltZwave(),
    DeviceLockUL3.model: DeviceLockUL3(),
    DeviceLockUL32ND.model: DeviceLockUL32ND(),
    DeviceLockUL300.model: DeviceLockUL300(),
    DeviceLockUBoltPro.model: DeviceLockUBoltPro(),
}