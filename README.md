# This repository
This repo contains four scripts that are mostly related to each other:

- `discord_launcher_gui.py`, a script for managing and launching Discord with a graphical user interface
- `discord_launcher.py`, a script for managing and launching Discord from a command-line interface
- `discord_launcher_lib.py`, a common library for the above two scripts
- `discord_update_lib.py`, a library that can download, update, and install Discord

# discord_launcher_gui
GUI interface written with GTK 3 to launch Discord and manage Discord updates. You can also use it to edit the launcher configuration.

# discord_launcher
Command-line interface to launch Discord and manage Discord updates.

```
usage: discord_launcher [-h] [--config [CONFIG FILE]] [--log-level [LOGGING LEVEL]]
                        {latest-version,installed-version,installed-channel,check-updates,stop,update,run,update-run,install,install-desktop-entry} ...

Launch and auto-update Discord.

positional arguments:
  {latest-version,installed-version,installed-channel,check-updates,stop,update,run,update-run,install,install-desktop-entry}
                        Operating mode.
    latest-version      Return the latest Discord version number.
    installed-version   Return the installed Discord version number.
    installed-channel   Return the installed Discord channel.
    check-updates       Check if the installed Discord has an update available.
    stop                Attempt to stop a running Discord launcher instance.
    update              Update Discord if there is an update available.
    run                 Run the Discord installation, WITHOUT updating. If there is an update available, Discord may refuse to work.
    update-run          Update if there is an update available and run the Discord installation.
    install             Install Discord. If there is an existing installation, it WILL be removed!
    install-desktop-entry
                        Install a Discord desktop entry. This will NOT work until you've installed Discord!

options:
  -h, --help            show this help message and exit
  --config [CONFIG FILE], -c [CONFIG FILE]
                        Use a custom config location. (default: /home/ethan/.local/share/discord_launcher/config.toml)
  --log-level [LOGGING LEVEL], -v [LOGGING LEVEL]
                        Set the logging level. Possible options are `debug`, `info`, `warn`, and `error`. (default: info)

Any unhandled arguments will be passed to Discord to OVERRIDE the configured launch options. To invoke this, you can pass `--`, and any following arguments will always be
unhandled.
```

## Setup
**This section is incomplete.** If you run into an issue while setting this up, please open an issue.

Minimum Python version 3.11 is required. You'll need to have GTK 3 installed before `discord_launcher_gui.py` will work. Both interfaces require GLib.

Create the venv:

```
$ python3 -m venv .
```

Run the launcher:  

```
$ python3 discord_launcher_gui.py
```

From here, edit the configuration as necessary. In particular, you may need to change the following values if they are not already acceptable:

- Discord Path (The path Discord will be installed in)
- Launcher Path (The path to the launcher script - this is automatically filled in, but plese verify that it's correct)
- Desktop Entry Path (Default installs in `~/.local/share/applications` but you can change it)

If you want to be able to access this screen again in the future, be sure that "Setup Action" is enabled. After the desktop entry is installed, right click Discord in your menu and click "Setup Launcher" to configure it.