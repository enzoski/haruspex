"""
This module handles reading from and writing to a FAT32 filesystem.
"""
import datetime
import struct
import sys


from collections import namedtuple

# TODO: read https://stackoverflow.com/questions/13775893/converting-struct-to-byte-and-back-to-struct
#       and research a bit into this being a reasonable way to convert bytes
#       into structs (and back).


ATTRIBUTES = {
    "read-only": 0x01, 
    "hidden"   : 0x02,
    "system"   : 0x04,
    "volume-id": 0x08,
    "directory": 0x10,
    "archive"  : 0x20,
}

ALL_ASCII = b"".maketrans(b"", b"")  # a bit hacky, but works
FILENAME_CHARS = b"0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ_- \xe5"
REMAIN_ASCII = ALL_ASCII
for idx, char in enumerate(FILENAME_CHARS):
    char = FILENAME_CHARS[idx:idx + 1]
    REMAIN_ASCII.replace(char, b"")
FILENAME_TRANS = b"".maketrans(
    REMAIN_ASCII,
    b"\x00" * len(REMAIN_ASCII)
)


def read_attributes(value):
    """
    Receives the raw attributes byte, and parses it into a dictionary based on
    the `ATTRIBUTES` constant.

    :param raw_attrs: bytes object, only the first byte is used
    :return: dictionary with the corresponding attributes and their bool value
    """
    ret = {}
    for k, v in ATTRIBUTES.items():
        ret[k] = bool(value & v)
    return ret


def read_time(bytes_, mili=0):
    """
    Receives time + date bytes in `bytes_`, and returns a datetime object.
    Checks for pathological 0xffff patterns.

    :param bytes_: raw value, made from the the time+date fields in the raw
        directory entry/file record.
    :param mili: the raw byte (int) that encodes the 10 milisecond precision,
        for timestamps that support it (defaults to 0)
    :return: datetime.datetime object
    """
    raw_time, raw_date, = struct.unpack("<HH", bytes_)
    # first we take care of the date
    year    = (raw_date >> 9) + 1980
    month   = (raw_date & 0b0000000111100000) >> 5
    day     =  raw_date & 0b0000000000011111
    # and now the time
    hour    =  raw_time >> 11
    minute  = (raw_time & 0b0000011111100000) >> 5
    second  = (raw_time & 0b0000000000011111) * 2
    second += mili // 100
    micros  = (mili % 100) * 1000
    # we know theres an issue in some Linux based systems that make
    # 0xffffffff datetimes for some FileRecords (that don't seem to belong to
    # the files, some kind of temporary record) so we must check a few things:
    if month >= 12:
        month = 12
    if hour >= 23:
        hour = 23
    if minute >= 59:
        minute = 59
    if second >= 59:
        second = 59
    # that gives a few sanity checks and should catch that particular issue
    dt = datetime.datetime(year, month, day, hour, minute, second, micros)
    return dt


class FileRecord:
    def __init__(self, data):
        self._raw_data   = data
        self._name       = b""
        self._ext        = b""
        self._size       = -1
        self._attributes = {k: False for k in ATTRIBUTES}
        self._flags      = 0  # reserved, should be 0 but different implementations may use it 
        self._cluster    = -1
        self._created    = None
        self._last_access= None
        self._modified   = None
        # that's all for real attributes of a file record
        self._parse()
    
    # The Properties
    # (these could be done with metaclasses, but...)
    @property
    def raw_data(self):
        return self._raw_data
    
    @raw_data.setter
    def raw_data(self, value):
        pass  # let's make it read-only for the moment

    @property
    def name(self):
        return self._name
    
    @name.setter
    def name(self, value):
        if isinstance(value, str):
            value = bytes(value, "utf-8")
        value = value.upper()  # not supporting long names for the moment
        name = value.translate(FILENAME_TRANS)
        name = name.replace(b"\x00", b"")
        name = name[:8],   # byebye long names!
        # let's check if there's an ext, for the lazy user
        if b"." in value:
            name, dot, ext = value.rpartition(b".")
            ext = ext.rstrip()
            self._ext = ext[-3:]
        name = name.rstrip()
        self._name = name # 
        # will not enforce name uniqueness, just being uppercase and within
        # valid range of characters
    
    @property
    def ext(self):
        return self._ext
    
    @ext.setter
    def ext(self, value):
        if isinstance(value, str):
            value = bytes(value, "utf-8")
        value = value.upper()
        ext = value.translate(FILENAME_TRANS)
        ext = ext.replace(b"\x00", b"")
        ext = ext.rstrip()
        ext = ext[:3]
        self._ext = ext
    
    @property
    def size(self):
        return self._size
    
    @size.setter
    def size(self, value):
        if value > 0xffffffff:
            value = 0xffffffff
        self._size = value
    
    @property
    def attributes(self):
        return self._attributes
    
    @attributes.setter
    def attributes(self, value):
        # let's cleanup in case the given dict has some extra keys
        value = {k:v for k, v in value.items() if k in ATTRIBUTES}
        self._attributes.update(value)
    
    @property
    def flags(self):
        return self._flags
    
    @flags.setter
    def flags(self, value):
        pass  # let's keep this read-only for the moment

    @property
    def cluster(self):
        return self._cluster
    
    @cluster.setter
    def cluster(self, value):
        if value > 0xffffffff:
            value = 0xffffffff
        # no point in checking anything else, the filesystem should make
        # sure there's no invalid cluster set
        self._cluster = value
    
    @property
    def created(self):
        return self._created
    
    @created.setter
    def created(self, value):
        # truth is, python's datetime object handles a lot already
        # so we only need to check the year
        year = value.year
        if year < 1980:
            year = 1980
            value = value.replace(year=year)
        elif year > 2107:
            year = 2107
            value = value.replace(year=year)
        micros = value.microsecond
        micros = (micros // 10000) * 10000  # let's be honest about the precision
        value.replace(microsecond=micros)
        self._created = value

    # last_access
    @property
    def last_access(self):
        return self._last_access
    
    @last_access.setter
    def last_access(self, value):
        # truth is, python's datetime object handles a lot already
        # so we only need to check the year
        year = value.year
        if year < 1980:
            year = 1980
            value = value.replace(year=year)
        elif year > 2107:
            year = 2107
            value = value.replace(year=year)
        # and here we erase the time
        value = value.replace(hour=0, minute=0, second=0, microsecond=0)
        # technically a date object at this point, but we prefer to keep
        # things consistent and have them all be datetime
        self._last_access = value

    @property
    def modified(self):
        return self._modified
    
    @modified.setter
    def modified(self, value):
        # truth is, python's datetime object handles a lot already
        # so we only need to check the year
        year = value.year
        if year < 1980:
            year = 1980
            value = value.replace(year=year)
        elif year > 2107:
            year = 2107
            value = value.replace(year=year)
        second = value.second
        second = (second // 2) * 2  # let's be honest about the precision
        value = value.replace(second=second)
        self._modified = value
    
    def __repr__(self):
        name = self.name.decode("utf-8")
        ext  = self.ext.decode("utf-8")
        return f"< DirectoryEntry: {name}.{ext}>"
    
    def __str__(self):
        name = self.name.decode("utf-8")
        ext  = self.ext.decode("utf-8")
        return (
               f"< DirectoryEntry: {name}.{ext}\n"
               f"    {'size':12}:{self.size:>12}\n" 
               f"    {'attributes':12}:{self._attrs2str():>12}\n" 
               f"    {'cluster':12}:{self.cluster:>12}\n" 
               f"    {'created':12}: {self.created}\n" 
               f"    {'last_access':12}: {self.last_access}\n" 
               f"    {'modified':12}: {self.modified}\n" 
               f">"
        )
    
    def _attrs2str(self):
        """
        Helper function for pretty-printing the attributes in __str__.
        """
        ret = [
            k[0].upper() if v else k[0]
            for (k, v) in self._attributes.items()
        ]
        return "".join(ret)
    
    def _parse(self):
        """
        Parses self._raw_data and updates the properties of the file.
        """
        # TODO: maybe it should be called load? or just parse?
        #       will define it better after having a dump() or save() method
        self.name        = self._raw_data[0:8] + b"." + self._raw_data[8:11]
        # we use the shorthand that sets name and extension in a single pass
        self.size,       = struct.unpack("<L", self._raw_data[28:32])
        self.attributes  = read_attributes(self._raw_data[11])
        self.flags       = self._raw_data[12]
        self.cluster,    = struct.unpack("<L", self._raw_data[26:28] + self._raw_data[20:22])
        self.created     = read_time(self._raw_data[14:18], mili=self._raw_data[13])
        self.last_access = read_time(b"\x00\x00" + self._raw_data[18:20])
        self.modified    = read_time(self._raw_data[22:26])


class Filesystem:
    pass
