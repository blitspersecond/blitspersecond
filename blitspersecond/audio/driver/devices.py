"""Output-device policy at the PortAudio boundary.

sounddevice is the transport on every platform. On Windows, PortAudio's
overall default can be a legacy MME view even when the useful endpoint is
available through WASAPI, so production explicitly selects WASAPI's current
default output. Omitting WASAPI-specific settings keeps the stream shared.
"""

import platform
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OutputDevice:
    index: int
    name: str
    host_api_index: int
    host_api_name: str
    max_output_channels: int
    default_samplerate: float
    default_low_output_latency: float
    default_high_output_latency: float

    @property
    def sounddevice_name(self):
        return f"{self.name}, {self.host_api_name}"


@dataclass(frozen=True, slots=True)
class WindowsOutputTopology:
    devices: tuple[OutputDevice, ...]
    portaudio_default: OutputDevice | None
    wasapi_default: OutputDevice

    @property
    def same_named_endpoint(self):
        default = self.portaudio_default
        if default is None:
            return False
        default_name = default.name.strip().casefold()
        wasapi_name = self.wasapi_default.name.strip().casefold()
        if default_name == wasapi_name:
            return True
        # WinMM's MAXPNAMELEN is 32 including the terminator.
        return (
            default.host_api_name.casefold() == "mme"
            and len(default_name) == 31
            and wasapi_name.startswith(default_name)
        )


def _output_device(index, devices, host_apis):
    info = devices[index]
    if int(info["max_output_channels"]) <= 0:
        raise ValueError(f"device {index} is not an output device")
    host_api_index = int(info["hostapi"])
    return OutputDevice(
        index=index,
        name=str(info["name"]),
        host_api_index=host_api_index,
        host_api_name=str(host_apis[host_api_index]["name"]),
        max_output_channels=int(info["max_output_channels"]),
        default_samplerate=float(info["default_samplerate"]),
        default_low_output_latency=float(info["default_low_output_latency"]),
        default_high_output_latency=float(info["default_high_output_latency"]),
    )


def query_windows_output_topology(sounddevice):
    """Return PortAudio's overall and WASAPI-specific default outputs."""
    host_apis = tuple(sounddevice.query_hostapis())
    devices = tuple(sounddevice.query_devices())
    outputs = tuple(
        _output_device(index, devices, host_apis)
        for index, info in enumerate(devices)
        if int(info["max_output_channels"]) > 0
    )
    by_index = {device.index: device for device in outputs}

    default_pair = sounddevice.default.device
    try:
        portaudio_default_index = int(default_pair[1])
    except (IndexError, TypeError):
        portaudio_default_index = int(default_pair)
    portaudio_default = by_index.get(portaudio_default_index)

    wasapi = next(
        (
            host_api
            for host_api in host_apis
            if str(host_api["name"]).casefold() == "windows wasapi"
        ),
        None,
    )
    if wasapi is None:
        raise LookupError(
            "PortAudio does not expose required Windows WASAPI; "
            "legacy host APIs are not supported fallbacks"
        )

    wasapi_default_index = int(wasapi["default_output_device"])
    if wasapi_default_index < 0:
        raise LookupError("Windows WASAPI has no default output device")
    try:
        wasapi_default = by_index[wasapi_default_index]
    except KeyError as error:
        raise LookupError(
            "Windows WASAPI's default is not an output device: "
            f"{wasapi_default_index}"
        ) from error
    if wasapi_default.host_api_name.casefold() != "windows wasapi":
        raise LookupError(
            "Windows WASAPI returned a default from another host API: "
            f"{wasapi_default.host_api_name}"
        )

    return WindowsOutputTopology(
        devices=outputs,
        portaudio_default=portaudio_default,
        wasapi_default=wasapi_default,
    )


def default_output_device(sounddevice, system=None):
    """Return sounddevice's production output selector for this platform."""
    current_system = platform.system() if system is None else system
    if current_system == "Windows":
        return query_windows_output_topology(sounddevice).wasapi_default.index
    return None
