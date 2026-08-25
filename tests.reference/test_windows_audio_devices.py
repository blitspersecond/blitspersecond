from blitspersecond.audio.driver.devices import (
    default_output_device,
    query_windows_output_topology,
)


class FakeDefault:
    device = [1, 2]


class FakeSounddevice:
    default = FakeDefault()

    @staticmethod
    def query_hostapis():
        return (
            {
                "name": "MME",
                "default_input_device": 1,
                "default_output_device": 2,
            },
            {
                "name": "Windows WASAPI",
                "default_input_device": 4,
                "default_output_device": 3,
            },
        )

    @staticmethod
    def query_devices():
        common = {
            "default_samplerate": 48000.0,
            "default_low_output_latency": 0.02,
            "default_high_output_latency": 0.1,
        }
        return (
            {
                **common,
                "name": "Mapper input",
                "hostapi": 0,
                "max_output_channels": 0,
            },
            {
                **common,
                "name": "Microphone",
                "hostapi": 0,
                "max_output_channels": 0,
            },
            {
                **common,
                "name": "USB Speakers",
                "hostapi": 0,
                "max_output_channels": 2,
            },
            {
                **common,
                "name": "USB Speakers",
                "hostapi": 1,
                "max_output_channels": 2,
            },
            {
                **common,
                "name": "WASAPI microphone",
                "hostapi": 1,
                "max_output_channels": 0,
            },
        )


def test_finds_wasapi_path_to_same_named_portaudio_default():
    topology = query_windows_output_topology(FakeSounddevice)

    assert [device.index for device in topology.devices] == [2, 3]
    assert topology.portaudio_default is not None
    assert topology.portaudio_default.index == 2
    assert topology.portaudio_default.host_api_name == "MME"
    assert topology.wasapi_default.index == 3
    assert topology.wasapi_default.host_api_name == "Windows WASAPI"
    assert topology.wasapi_default.sounddevice_name == (
        "USB Speakers, Windows WASAPI"
    )
    assert topology.same_named_endpoint


def test_matches_mme_name_truncated_to_31_characters():
    class TruncatedMmedefault:
        default = FakeDefault()

        @staticmethod
        def query_hostapis():
            return FakeSounddevice.query_hostapis()

        @staticmethod
        def query_devices():
            devices = [
                dict(device) for device in FakeSounddevice.query_devices()
            ]
            devices[2]["name"] = "Headphones (MAJOR III BLUETOOTH"
            devices[3]["name"] = "Headphones (MAJOR III BLUETOOTH)"
            return tuple(devices)

    topology = query_windows_output_topology(TruncatedMmedefault)

    assert topology.portaudio_default is not None
    assert len(topology.portaudio_default.name) == 31
    assert topology.same_named_endpoint


def test_rejects_missing_wasapi_host_api():
    class NoWasapi:
        default = FakeDefault()

        @staticmethod
        def query_hostapis():
            return FakeSounddevice.query_hostapis()[:1]

        @staticmethod
        def query_devices():
            return FakeSounddevice.query_devices()[:3]

    try:
        query_windows_output_topology(NoWasapi)
    except LookupError as error:
        assert str(error) == (
            "PortAudio does not expose required Windows WASAPI; "
            "legacy host APIs are not supported fallbacks"
        )
    else:
        raise AssertionError("missing WASAPI should fail")


def test_production_policy_selects_wasapi_default_on_windows():
    assert default_output_device(FakeSounddevice, system="Windows") == 3


def test_production_policy_leaves_portaudio_default_on_linux():
    assert default_output_device(FakeSounddevice, system="Linux") is None
