# Resuming the lanes after a reboot

Paused 2026-09-20 20:37 with all three panes idle (interrupted mid-goal; goals survive
`--resume`). The tree has uncommitted work in progress from the build and gtm lanes; it is on
disk and the lanes continue from it.

```bash
cd ~/workspace/hx && tmux new -d -s build-0 -c ~/workspace/hx && tmux send-keys -t build-0 'claude --resume fd0b9855-8ec7-4d2f-b5e6-b40c1d6cbca1' Enter
cd ~/workspace/hx && tmux new -d -s ui-1    -c ~/workspace/hx && tmux send-keys -t ui-1    'claude --resume 1fef6c16-19b0-464a-9bb8-905f654ba0a5' Enter
cd ~/workspace/hx && tmux new -d -s gtm-2   -c ~/workspace/hx && tmux send-keys -t gtm-2   'claude --resume 0bd6d45e-b525-4cb5-8154-62e4ac2dc927' Enter
```

Goals in flight: build-7 (the v1 delete pass), ui-8 (restore the autodev UI on hx data),
gtm-9 (README, skills, personas wired, hx-fleet). After resuming, each lane needs one line
typed into its pane: "continue your goal". The orchestrator session re-arms its monitor when
resumed.
