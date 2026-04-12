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

The parameters for sample rate and band shift are taken from the extended WAV header or the filename of typical COHIRADIA recording files.
