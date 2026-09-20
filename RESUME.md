# Resuming the lanes after a reboot

All three lanes were interrupted at an idle prompt on 2026-09-20 with a clean tracked tree.
Recreate the tmux sessions in this directory and resume each Claude Code conversation by id:

```bash
cd ~/workspace/hx && tmux new -d -s build-0 -c ~/workspace/hx && tmux send-keys -t build-0 'claude --resume 175996de-ed3a-4edc-b018-8a36aa880b1e' Enter
cd ~/workspace/hx && tmux new -d -s ui-1    -c ~/workspace/hx && tmux send-keys -t ui-1    'claude --resume cd595057-fbf9-4c6e-ac74-30de1b461e27' Enter
cd ~/workspace/hx && tmux new -d -s gtm-2   -c ~/workspace/hx && tmux send-keys -t gtm-2   'claude --resume 0bd6d45e-b525-4cb5-8154-62e4ac2dc927' Enter
```

`/goal` is restored by `--resume` (spec 01.1). The build lane was mid build-3; its goal comes
back with the conversation and it continues. ui and gtm had no active goal (ui-4 and gtm-4
were complete; ui-5 and gtm-5 wait on build-3 and build-4). The orchestrator session re-arms
its monitor when resumed.
