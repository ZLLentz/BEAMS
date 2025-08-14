"""
This module defines a config file format for beams clients and services.

The server and the client can use the same config file.

The config file is the basic configparser/ini format and may look
something like:

beams.cfg

[server]
host = my-favorite-server
port = 5001
"""

import configparser
import dataclasses
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class BeamsConfig:
    """
    The information used to configure beams.

    The same configuration is used to configure the server and to configure the client.
    """
    host: str = "localhost"
    port: int = 50051


class ConfigError(RuntimeError):
    """Generic beams-specific error while trying to load a config."""
    ...


class ConfigDiscoveryError(ConfigError):
    """Error raised by beams if the config file cannot be found."""
    ...


class ConfigMalformedError(ConfigError):
    """Error raised by beams if there are issues with the contents of the config file."""
    ...


def find_config() -> str:
    """
    Return the directory that contains the user's beams.cfg file.

    In order of priority:

    1. The full path in the $BEAMS_CFG environment variable
    2. A file named beams.cfg in the current working directory
    3. A file named beams.cfg in the directory specified by the $XDG_CONFIG_HOME environment variable.
    4. A file named beams.cfg in the ~/.config directory.

    If no config file can be found, this raises a ConfigDiscoveryError.

    Returns
    -------
    str
        The full path the config file, e.g. "~/.config/beams.cfg"

    Raises
    ------
    ConfigDiscoveryError
        If no beams configuration file can be found in any of the normal locations.
    """
    # Environment variable first
    beams_cfg = os.environ.get("BEAMS_CFG", "")
    if beams_cfg:
        logger.debug("Found $BEAMS_CFG specification at %s", beams_cfg)
        return beams_cfg
    # Working directory next
    config_dirs = ["."]
    # General config directories last
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config is not None:
        config_dirs.append(xdg_config)
    config_dirs.append(os.path.expanduser("~/.config"))
    for directory in config_dirs:
        logger.debug("Searching for Beams config in %s", directory)
        full_path = os.path.join(directory, "beams.cfg")
        if os.path.exists(full_path):
            logger.debug("Found configuration file at %r", full_path)
            return full_path
    # Give up
    raise ConfigDiscoveryError("No beams configuration file found. Check BEAMS_CFG.")


def load_config(config_file: Optional[str] = None) -> BeamsConfig:
    """
    Returns the beams configuration object.

    You can either pass a specific filepath or let the discovery mechanism find the
    config for you.

    Parameters
    ----------
    config_file : str, optional
        The path to a specific config file to load from. If omitted, we'll search
        for a config file in the standard paths.

    Returns
    -------
    BeamsConfig
        The config dataclass that represents the data from the file.

    Raises
    ------
    ConfigDiscoveryError
        If no beams configuration file can be found in any of the normal locations.
    ConfigMalformedError
        If a configuration file was found, but does not have the expected contents.
    """
    if config_file is None:
        config_file = find_config()
    config_parser = configparser.ConfigParser()
    files_read = config_parser.read(config_file)
    if not files_read:
        raise ConfigMalformedError(f"Cannot read malformed config file {config_file}")
    try:
        return BeamsConfig(
            host=config_parser["server"]["host"],
            port=int(config_parser["server"]["port"]),
        )
    except KeyError as exc:
        raise ConfigMalformedError("Config missing required keys") from exc


def load_config_or_default(config_file: Optional[str] = None, allow_no_config: bool = False) -> BeamsConfig:
    """
    Returns the beams configuration or a default configuration.

    This is a convenience function for entrypoints that don't care if the user
    has a configuration file or not and are OK with the default configuration
    being used.

    A warning will be logged if we used the default configuration.

    Parameters
    ----------
    config_file : str, optional
        The path to a specific config file to load from. If omitted, we'll search
        from a config file.
    allow_no_config : bool, optional
        If False (default), raise if we cannot discover a config.
        If True, use the default config when we cannot discover a config.
        This has no effect if config_file is specifically chosen via argument here,
        because in these cases no discovery will take place.

    Returns
    -------
    BeamsConfig
        The config dataclass that represents the data from the file.

    Raises
    ------
    ConfigDiscoveryError
        If no beams configuration file can be found in any of the normal locations
        and allow_no_config is False.
    ConfigMalformedError
        If a configuration file was found, but does not have the expected contents.
    """
    try:
        return load_config(config_file=config_file)
    except ConfigDiscoveryError:
        if allow_no_config:
            logger.debug("Error traceback from discovering beams config", exc_info=True)
            logger.warning("No valid beams config found, using defaults")
            return BeamsConfig()
        raise
