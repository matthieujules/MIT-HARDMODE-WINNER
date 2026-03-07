# Device SSH Access

This file records the SSH connection details for the four Raspberry Pi devices.

The current IP addresses come from the existing device configs in this repo.
Hostnames and passwords still need to be filled in with the real values.

## Lamp

- Device folder: `devices/lamp`
- Hostname: `lamphost`
- IP address: `100.82.116.53`
- SSH user: `lamp`
- SSH password: `lamp`
- Sync target on Pi: `~/Desktop/lamp`

## Mirror

- Device folder: `devices/mirror`
- Hostname: `mirrorhost`
- IP address: `100.71.104.73`
- SSH user: `mirror`
- SSH password: `mirror`
- Sync target on Pi: `~/Desktop/mirror`

## Radio

- Device folder: `devices/radio`
- Hostname: `radiohost`
- IP address: `100.119.150.35`
- SSH user: `radio`
- SSH password: `radio`
- Sync target on Pi: `~/Desktop/radio`

## Rover

- Device folder: `devices/rover`
- Hostname: `roverhost`
- IP address: `100.81.100.34`
- SSH user: `rover`
- SSH password: `rover`
- Sync target on Pi: `~/Desktop/rover`

## Notes

- `devices/sync.sh` uses the IP addresses above by default.
- If you prefer hostnames, set `LAMP_HOST`, `MIRROR_HOST`, `RADIO_HOST`, and `ROVER_HOST` before running the script.
- The script uses `rsync` over SSH and will prompt for the SSH password unless SSH keys are configured.
