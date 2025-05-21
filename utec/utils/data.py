"""Data conversion utilities for the U-tec library."""

import datetime
import struct
from typing import Optional, Union, Any


def date_from_4bytes(byte_array: bytes) -> Optional[datetime.datetime]:
    """Convert 4 bytes to a datetime object.
    
    Args:
        byte_array: Byte array to convert.
        
    Returns:
        Datetime object or None if the conversion fails.
    """
    if byte_array is None or len(byte_array) < 4:
        return None

    byte_to_int4 = struct.unpack('>I', byte_array[:4])[0]
    seconds = byte_to_int4 & 63
    year = ((byte_to_int4 >> 26) & 63) + 2000
    month = ((byte_to_int4 >> 22) - 1) & 15
    day = (byte_to_int4 >> 17) & 31
    hour = (byte_to_int4 >> 12) & 31
    minute = (byte_to_int4 >> 6) & 63

    return datetime.datetime(year, month, day, hour, minute, seconds)


def bytes_to_int2(byte_array: bytes) -> int:
    """Convert 2 bytes to an integer.
    
    Args:
        byte_array: Byte array to convert.
        
    Returns:
        Integer value.
    """
    result = 0
    for i in range(1, -1, -1):
        result = (result << 8) | (byte_array[i] & 0xFF)
    return result


def byte_to_int4(byte_array: bytes, i: int) -> int:
    """Convert 4 bytes to an integer.
    
    Args:
        byte_array: Byte array to convert.
        i: Starting index.
        
    Returns:
        Integer value.
    """
    result = 0
    if byte_array is None:
        return 0
    for i3 in range(3, -1, -1):
        result = (result << 8) | (byte_array[i + i3] & 0xFF)
    return result


def bytes_to_ascii(bArr: bytearray) -> Optional[str]:
    """Convert bytes to ASCII string.
    
    Args:
        bArr: Byte array to convert.
        
    Returns:
        ASCII string or None if the conversion fails.
    """
    i = 0
    i2 = len(bArr)
    if not bArr or i < 0 or i2 <= 0 or i >= len(bArr) or len(bArr) - i < i2:
        return None

    substring = bArr[i:i+i2]
    if 0 in substring:
        substring = substring[:substring.index(0)]
    try:
        return substring.decode("ISO8859-1")
    except UnicodeDecodeError:
        return None


def to_byte_array(value: int, size: int) -> bytearray:
    """Convert an integer to a byte array.
    
    Args:
        value: Integer value to convert.
        size: Size of the byte array.
        
    Returns:
        Byte array.
    """
    byte_array = bytearray(size)
    for i in range(4):
        if i < size:
            byte_array[i] = (value >> (i * 8)) & 0xFF
    return byte_array


def decode_password(password: Union[int, str]) -> str:
    """Decode the password that the API returns to the Admin Password.
    
    Args:
        password: Password to decode.
        
    Returns:
        Decoded password.
    """
    if isinstance(password, str):
        return password
        
    try:
        if password == 0:
            return "0"
            
        byte_array = bytearray(4)
        i3 = 0
        while i3 < 4:
            byte_array[i3] = (password >> (i3 * 8)) & 255
            i3 += 1

        str2 = ""
        length = len(byte_array) - 1
        while length >= 0:
            hex_string = format(byte_array[length] & 0xFF, '02x')
            length -= 1
            if len(hex_string) == 1:
                hex_string = "0" + hex_string
            str2 = str2 + hex_string
        parse_int = int(str2[0])
        if parse_int == 0:
            return str(password)
        str3 = str(int(str2[1:], 16))
        if parse_int != len(str3):
            str4 = str3
            count = 0
            while count < (parse_int - len(str3)):
                str4 = "0" + str4
                count += 1
            return str4
        return str3
    except Exception as e:
        print(f"Error decoding password: {e}")
        return str(password)


class DeviceNotAvailable(Exception):
    """Exception raised when a device is not available."""
    pass