## Scheduled Gmail scanner

The Gmail discovery stage can run automatically through a `systemd --user`
timer.

The scheduled scanner runs:

    python src/gmail_scan.py --scheduled

Unlike the manual scan, scheduled mode does not rescan the entire inbox.

It scans:

- at least the previous 24 hours;
- or from the last successful scheduled scan, if that happened more than
  24 hours ago.

The timestamp of the last successful scheduled run is stored in:

    data/scan_state.json

Example:

    {
      "last_successful_scan": "2026-09-24T21:01:35.946425+00:00"
    }

The scan state is updated only after a successful run. If the computer is
offline or the Gmail scan fails, the next scheduled execution searches back
to the previous successful timestamp.

### systemd user service

Service file:

    ~/.config/systemd/user/substack-scan.service

Example:

    [Unit]
    Description=Substack Gmail Scanner

    [Service]
    Type=oneshot
    WorkingDirectory=/home/gabriel/Projetos/substack-pdf-archiver
    ExecStart=/home/gabriel/Projetos/substack-pdf-archiver/.venv/bin/python /home/gabriel/Projetos/substack-pdf-archiver/src/gmail_scan.py --scheduled

Timer file:

    ~/.config/systemd/user/substack-scan.timer

Current configuration:

    [Unit]
    Description=Run Substack Gmail Scanner Daily

    [Timer]
    OnCalendar=*-*-* 18:00:00
    Persistent=true

    [Install]
    WantedBy=timers.target

This runs the Gmail scanner daily at 18:00 local time.

`Persistent=true` means that if the scheduled execution is missed because the
computer is offline, systemd runs the timer when it becomes available again.

Enable and start the timer:

    systemctl --user daemon-reload
    systemctl --user enable --now substack-scan.timer

Check its status:

    systemctl --user status substack-scan.timer

List the next scheduled execution:

    systemctl --user list-timers substack-scan.timer

Disable it:

    systemctl --user disable --now substack-scan.timer

The scheduled task only performs Gmail discovery. It does not automatically
approve articles, open Firefox, resolve PDF URLs, or download PDFs. Those
stages remain explicitly user-triggered.
