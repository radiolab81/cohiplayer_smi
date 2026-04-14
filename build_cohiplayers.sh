#!/bin/bash
sudo apt install libncurses5-dev libncursesw5-dev libliquid-dev
g++ -O3 -o cohiplayer_smi_cmdline cohiplayer_smi_cmdline.cpp -lliquid -lm -lncurses
g++ -O3 -o cohiplayer_smi_tui cohiplayer_smi_tui.cpp -lliquid -lm -lncurses
g++ -O3 -o cohiplayer_smi_tui_mt cohiplayer_smi_tui_mt.cpp -lliquid -lm -lncurses
