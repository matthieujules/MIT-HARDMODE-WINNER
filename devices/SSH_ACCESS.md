# Device SSH Access

This file records the SSH connection details for the four Raspberry Pi devices.

The current IP addresses come from the existing device configs in this repo.
Hostnames and passwords still need to be filled in with the real values.

## Lamp

- Device folder: `devices/lamp`
- Hostname: `TBD`
- IP address: `192.168.1.101`
- SSH user: `pi`
- SSH password: `TBD`
- Sync target on Pi: `~/Desktop/lamp`

## Mirror

- Device folder: `devices/mirror`
- Hostname: `TBD`
- IP address: `192.168.1.102`
- SSH user: `pi`
- SSH password: `TBD`
- Sync target on Pi: `~/Desktop/mirror`

## Radio

- Device folder: `devices/radio`
- Hostname: `TBD`
- IP address: `192.168.1.103`
- SSH user: `pi`
- SSH password: `TBD`
- Sync target on Pi: `~/Desktop/radio`

## Rover

- Device folder: `devices/rover`
- Hostname: `TBD`
- IP address: `192.168.1.104`
- SSH user: `pi`
- SSH password: `TBD`
- Sync target on Pi: `~/Desktop/rover`

## Notes

- `devices/sync.sh` uses the IP addresses above by default.
- If you prefer hostnames, set `LAMP_HOST`, `MIRROR_HOST`, `RADIO_HOST`, and `ROVER_HOST` before running the script.
- The script uses `rsync` over SSH and will prompt for the SSH password unless SSH keys are configured.
