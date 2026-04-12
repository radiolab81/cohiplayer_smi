# COHIRADIA IQ-WAV-File Player based on liquiddsp 
This is a small player software for the COHIRADIA project by Hermann Scharfetter.

https://www.cohiradia.org/de/

https://www.radiomuseum.org/dsp_cohiradia.cfm

It serves as a standalone program, developed as a demonstrator for smisdr (https://github.com/radiolab81/smisdr). 

Based on liquid-dsp (https://liquidsdr.org/), it can stream IQ-WAV Cohiradia recordings from an external PC to the smisdr via Ethernet, and can also be installed directly on the smisdr (Raspberry Pi 4). It supports the entire range of DACs provided by the smisdr (from 8 to 16 bit RF-DACs).

There are two versions of the program, a command-line version:

```console
./cohiplayer_smi_cmdline 
Benutzung: ./cohiplayer_smi_cmdline <datei.wav> <Ziel-IP> [Bitbreite (8,10,12,14,16 - Standard 8)]
```
and with ./cohiplayer_smi_tui a version with TUI based on ncurses. This provides a lightweight file browser to select the IQ file to be played.

![main1](https://github.com/radiolab81/cohiplayer_smi/blob/main/www/fb1.jpg)

![main2](https://github.com/radiolab81/cohiplayer_smi/blob/main/www/fb2.jpg)

The parameters for sample rate and band shift are taken from the extended WAV header or the filename of typical COHIRADIA recording files (https://www.cohiradia.org/de/archiv/ or https://www.radiomuseum.org/dsp_cohiradia.cfm#recording).

![main3](https://github.com/radiolab81/cohiplayer_smi/blob/main/www/player.jpg)

Transmission over smisdr-device is done via DMA (Raspberry Pi with DAC connected to Secondary Memory Interface), there is usually enough computing power available to install this player directly on the smisdr.

![main4](https://github.com/radiolab81/smisdr/blob/main/www/schematic.jpg)


![main5](https://github.com/radiolab81/cohiplayer_smi/blob/main/www/htop_cohi_on_rpi.jpg)

As you can see, there is almost no CPU load in the overall system during playback. This system is therefore suitable for building very small and portable players for the COHIRADIA project (or similar SDR-based content). If required, the reverse direction, i.e., recording COHIRADIA files in the desired bit depth of the SMI bus (8-16 bits), can also be implemented quickly.

### repo-structure
- `README.md`: This file
- `cohiplayer_smi_cmdline.cpp`: a vy simple command-line version of cohiradia player. You can build it with `build_cohiplayers.sh`
- `cohiplayer_smi_tui.cpp`: TUI/ncurses-version of cohiradia player. You can build it with `build_cohiplayers.sh`
- `COHI_fft_check_8.py`: Debug tool to stream directly from the player to an FFT for displaying the RF spectrum (8 Bit Version).
- `COHI_fft_check_16.py`: Debug tool to stream directly from the player to an FFT for displaying the RF spectrum (16 Bit Version).

