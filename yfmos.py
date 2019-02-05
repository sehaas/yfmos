#!/usr/bin/env python
"""
    Control your Somfy receivers with a Sonoff RF Bridge
    Generate RfRaw B0 commands from a sniffed B1 string

    Copyright 2019 Sebastian Haas <sehaas@deebas.com>
    https://github.com/sehaas/yfmos
    License: MIT
"""

import sys
import os
import argparse
from ConfigParser import SafeConfigParser
from enum import Enum

class ManchesterEncode:
    def init(self, longPulse, shortPulse):
        self.longPulse = str(longPulse)
        self.shortPulse = str(shortPulse)
        self.encoded = ""

    def addData(self, bitvec):
        prev = bitvec[0]
        for i in range(1,len(bitvec)):
            if bitvec[i] == prev:
                self.encoded += (self.shortPulse * 2)
            else:
                self.encoded += self.longPulse
            prev = bitvec[i]

    def get_encoded(self):
        return self.encoded

class Commands(Enum):
    MY = 0x10
    UP = 0x20
    DOWN = 0x40
    PROG = 0x80

    def __str__(self):
        return self.name

    @staticmethod
    def from_string(s):
        try:
            return Commands[s]
        except KeyError:
            raise ValueError()

class Yfmos(object):
    CONFIG_FILE='.yfmosrc'

    def __init__(self):
        parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''\
Available commands:
    init	Initalize the remote by with a B1 data string
    gen		Generate B0 data string
    run		Run command on Tasmota RF bridge
    print	Print payload data
''')
        parser.add_argument('command', help='Subcommand to run')
        args = parser.parse_args(sys.argv[1:2])
        if not hasattr(self, args.command):
            print 'Unrecognized command'
            parser.print_help()
            exit(1)
        getattr(self, args.command)()

    def init(self):
        print('TBD - pars B1 string and init config/profile')

    def gen(self):
        parser = argparse.ArgumentParser(
        prog='%s gen' % os.path.basename(sys.argv[0]),
        description='Generate B0 data string')
        parser.add_argument('command', type=Commands.from_string, choices=list(Commands))
        parser.add_argument('--repeat', '-r', type=int, default=1)
        parser.add_argument('--profile', '-p', default='main')
        args = parser.parse_args(sys.argv[2:])

        config = SafeConfigParser()
        config.read(self.CONFIG_FILE)
        buckets = map(int, config.get(args.profile, 'buckets').split(','))
        device = int(config.get(args.profile, 'Device'), 0)
        rollingCode = config.getint(args.profile, 'RollingCode') + 1

        payload = self.__gen_payload(args.command, rollingCode, device)
        payload = self.__calc_checksum(payload)
        self.__printFrame(payload)
        payload = self.__obfuscate(payload)
        bitvec = self.__to_bitvec(payload)

        encoder = ManchesterEncode()
        encoder.init(config.get(args.profile, 'Long'), config.get(args.profile, 'Short'))
        encoder.addData(bitvec)
        dataStr = encoder.get_encoded()
        # FIXME: generate HWSync/SWSync string
        tmpStr = "05 %02X %04X %04X %04X %04X %04X 0000000000000012%s34" % (args.repeat,
            buckets[0], buckets[1], buckets[2], buckets[3], buckets[4], dataStr)
        strLen = int(len(tmpStr.replace(' ','')) / 2)
        print("RfRaw AA B0 %02X %s 55" % (strLen, tmpStr))

        config.set(args.profile, 'RollingCode', str(rollingCode))
        with open(self.CONFIG_FILE, 'wb') as configfile:
            config.write(configfile)

    def run(self):
        print("TBD - post B0 directly to Tasmota device")

    def __auto_int(self, x):
        return int(x, 0)

    def __gen_payload(self, cmd, code, device):
        payload = {}
        payload[0] = 0xA1
        # Command
        payload[1] = cmd.value & 0xF0
        # Rollingcode
        payload[2] = (code>>8) & 0xFF
        payload[3] = code & 0xFF
        # device ID
        payload[4] = (device>>16) & 0xFF
        payload[5] = (device>>8) & 0xFF
        payload[6] = device & 0xFF
        return payload

    def __calc_checksum(self, data):
        checksum = 0
        for i in range(len(data)):
            checksum = checksum ^ data[i] ^ (data[i] >> 4)
        data[1] |= (checksum & 0x0F)
        return data

    def __obfuscate(self, data):
        for i in range(1,len(data)):
            data[i] = data[i] ^ data[i-1]
        return data

    def __to_bitvec(self, data):
        out = (data[0]<<48 | data[1]<<40 | data[2] << 32 | data[3] << 24
            | data[4] << 16 | data[5] << 8 | data[6])
        return bin(out)[2:]

    def __printFrame(self, frame):
        print("Group       A       B       C       D       F               G                    ")
        print("Byte:       0H      0L      1H      1L      2       3       4       6       7    ")
        print("        +-------+-------+-------+-------+-------+-------+-------+-------+-------+")
        print("        !  0xA  + R-KEY ! C M D + C K S !  Rollingcode  ! Remote Handheld Addr. !")
        print("        !  0x%01X  +  0x%01X  !  0x%01X  +  0x%01X  !    0x%04X     !       0x%06X        !" % (
            (frame[0]>>4) & 0xF, frame[0] & 0xF, (frame[1]>>4) & 0xF, frame[1] & 0xF,
            (frame[2]<<8) + frame[3], (frame[4]<<16) + (frame[5]<<8) + frame[6]))
        print("        +-------+-------+-------+-------+MSB----+----LSB+LSB----+-------+----MSB+")


if __name__ == '__main__':
    Yfmos()
