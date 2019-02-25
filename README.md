# yfmos - generate Somfy RC commands #

Control your Somfy receivers with a Sonoff RF Bridge.
Generate RfRaw B0 commands from a sniffed B1 string.

## Setup ##
```
git clone https://github.com/sehaas/yfmos
cd yfmos
virtualenv .
. ./bin/activate
pip install -r requirements.txt
```
## Usage ##

Put your Sonoff RF Bridge in RfRaw sniffing mode and long-press one of the buttons
on you Somfy remote.

### Initialize yfmos ###

Initialize new profile with a custom (unique) device ID. The B1 string is mainly used to initialize the bucket timings.
If the device ID is omitted, the original device ID will be used.
```
python yfmos.py init --device 0xaabbcc --rollingcode 0 --profile kitchen AA B1 05 09F6 12CA 04EC 0276 68BA 000000000000001222233333323323333332332223333332332223333332233332233332233222333322332222222234 55
```

Generate a new pairing code.
```
python yfmos.py gen --profile kitchen --repeat 8 --command PROG
```

Put your receiver in programming mode (holding PROG on an already paired remote until the shades make a sharp movement).
Broadcast the resulting B0 string with the Sonoff RF Bride - the shades should confirm the new remote with a movement.

## Tested Hardware ###

* **Smoove origin RTS** sends out a parsable B1 string
* **Telis 16 RTS** did not produce a valid B1 string

## ToDo ##
- Send B0 command direct to the Tasmota web interface
- Generate HWSync / SWSync part
- Validate inputs / config

## Contributing ##
Please feel free to submit feedback, [bug reports](https://github.com/sehaas/yfmos/issues/new) or [pull requests](https://github.com/sehaas/yfmos/compare)

## Contributors ##

* **[Altelch](https://github.com/altelch)**
  * B1 decoder

* **[Sebastian Haas](https://github.com/sehaas)**
  * B0 encoder
