# Uninstalling Yaffo

Use the uninstall helper before removing the Python package. It cleans up the
desktop shortcut, downloaded runtime assets, and logs, then asks whether to
remove Yaffo's user data.

## Quit Yaffo

If Yaffo is running, quit it from the tray/menu icon:

- **Windows:** notification area at the right side of the taskbar.
- **macOS:** menu bar near the clock and system status icons.
- **Linux:** system tray or status area, depending on your desktop environment.

Choose **Quit Yaffo**.

## Run the Uninstall Helper

Open a terminal or command prompt and run:

```shell
yaffo uninstall
```

The helper removes:

- the Yaffo desktop/app shortcut, if one was installed;
- downloaded runtime assets, including models and bundled tools;
- Yaffo log files.

It then asks whether to delete Yaffo user data.

## Decide Whether to Keep User Data

Yaffo user data includes the local database, queue database, configuration,
thumbnails, cache, temporary files, trash, and backups. Your original photo and
video folders are not Yaffo user data and are not removed by this command.

Choose **no** if you might reinstall Yaffo later and want to keep your indexed
library state.

Choose **yes** if you want to remove Yaffo's local state completely.

## Remove the Python Package

After the uninstall helper finishes, remove the pipx-managed Yaffo environment:

```shell
pipx uninstall yaffo
```

pipx removes Yaffo's dedicated virtual environment and the command-line entry
points it installed.

If you chose to install Yaffo into your own virtual environment with pip instead
of using pipx, activate that environment and run:

```shell
python -m pip uninstall yaffo
```

## Reinstalling Later

If you kept user data, reinstalling Yaffo and running `yaffo setup` can reuse
the existing database and configuration.

If you deleted user data, Yaffo starts fresh the next time you install it.
