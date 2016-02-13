"""
Sample mixer and sequencer meant to create rhythms. Inspired by the Roland TR-909.
Uses PyAudio (https://pypi.python.org/pypi/PyAudio) for playing sound. On windows
it can fall back to using the winsound module if pysound isn't available.

Sample mix rate is configured at 44.1 khz. You may want to change this if most of
the samples you're using are of a different sample rate (such as 48Khz), to avoid
the slight loss of quality due to resampling.

Written by Irmen de Jong (irmen@razorvine.net) - License: MIT open-source.
"""
import sys
import os
import wave
import audioop
import time
import contextlib
try:
    import pyaudio
except ImportError:
    pyaudio = None
    import winsound
import cmd
if sys.version_info < (3, 0):
    raise RuntimeError("This module requires python 3.x")
from configparser import ConfigParser

__all__ = ["Sample", "Mixer", "Song", "Repl"]


class Sample(object):
    """
    Audio sample data, usually normalized to a fixed set of parameters: 16 bit stereo 44.1 Khz
    To avoid easy mistakes and problems, it is not possible to directly access the audio sample frames.
    All operations that manipulate the sample frames are implemented as methods on the object.
    """
    norm_samplerate = 44100
    norm_nchannels = 2
    norm_sampwidth = 2

    def __init__(self, frames=b"", wave_file=None, duration=0):
        self.locked = False
        if wave_file:
            self.load_wav(wave_file)
            self.filename = wave_file
        else:
            self.samplerate = self.norm_samplerate
            self.nchannels = self.norm_nchannels
            self.sampwidth = self.norm_sampwidth
            self.__frames = frames
            self.filename = None
        if duration > 0:
            if len(frames) > 0:
                raise ValueError("cannot specify a duration if frames are provided")
            self.append(duration)

    def dup(self):
        copy = Sample(self.__frames)
        copy.sampwidth = self.sampwidth
        copy.samplerate = self.samplerate
        copy.nchannels = self.nchannels
        copy.filename = self.filename
        copy.locked = False
        return copy

    def lock(self):
        self.locked = True
        return self

    @property
    def duration(self):
        return len(self.__frames) / self.samplerate / self.sampwidth / self.nchannels

    def frame_idx(self, seconds):
        return self.nchannels*self.sampwidth*int(self.samplerate*seconds)

    def load_wav(self, file):
        assert not self.locked
        with contextlib.closing(wave.open(file)) as w:
            if not 2 <= w.getsampwidth() <= 4:
                raise IOError("only supports sample sizes of 2, 3 or 4 bytes")
            if not 1 <= w.getnchannels() <= 2:
                raise IOError("only supports mono or stereo channels")
            self.__frames = w.readframes(w.getnframes())
            self.nchannels = w.getnchannels()
            self.samplerate = w.getframerate()
            self.sampwidth = w.getsampwidth()
            return self

    def write_wav(self, file):
        with contextlib.closing(wave.open(file, "wb")) as out:
            out.setparams((self.nchannels, self.sampwidth, self.samplerate, 0, "NONE", "not compressed"))
            out.writeframes(self.__frames)

    def write_stream(self, stream):
        stream.write(self.__frames)

    def normalize(self):
        assert not self.locked
        if self.samplerate != self.norm_samplerate:
            # Convert sample rate. Note: resampling causes slight loss of sound quality.
            self.__frames = audioop.ratecv(self.__frames, self.sampwidth, self.nchannels, self.samplerate, self.norm_samplerate, None)[0]
            self.samplerate = self.norm_samplerate
        if self.sampwidth != self.norm_sampwidth:
            # Convert to 16 bit sample size.
            # Note that Python 3.4+ is required to support 24 bits sample sizes.
            self.__frames = audioop.lin2lin(self.__frames, self.sampwidth, self.norm_sampwidth)
            self.sampwidth = self.norm_sampwidth
        if self.nchannels == 1:
            # convert to stereo
            self.__frames = audioop.tostereo(self.__frames, self.sampwidth, 1, 1)
            self.nchannels = 2
        return self

    def make_32bit(self, scale_amplitude=True):
        assert not self.locked
        self.__frames = self.get_32bit_frames(scale_amplitude)
        self.sampwidth = 4
        return self

    def get_32bit_frames(self, scale_amplitude=True):
        if self.sampwidth == 4:
            return self.__frames
        frames = audioop.lin2lin(self.__frames, self.sampwidth, 4)
        if not scale_amplitude:
            # we need to scale back the sample amplitude to fit back into 16 bit range
            factor = 1.0/2**(8*abs(self.sampwidth-4))
            frames = audioop.mul(frames, 4, factor)
        return frames

    def make_16bit(self, maximize_amplitude=True):
        assert not self.locked
        assert self.sampwidth >= 2
        if maximize_amplitude:
            self.amplify_max()
        if self.sampwidth > 2:
            self.__frames = audioop.lin2lin(self.__frames, self.sampwidth, 2)
            self.sampwidth = 2
        return self

    def amplify_max(self):
        assert not self.locked
        max_amp = audioop.max(self.__frames, self.sampwidth)
        max_target = 2 ** (8 * self.sampwidth - 1) - 2
        if max_amp > 0:
            factor = max_target/max_amp
            self.__frames = audioop.mul(self.__frames, self.sampwidth, factor)
        return self

    def amplify(self, factor):
        assert not self.locked
        self.__frames = audioop.mul(self.__frames, self.sampwidth, factor)
        return self

    def cut(self, start_seconds, end_seconds):
        assert not self.locked
        assert end_seconds > start_seconds
        start = self.frame_idx(start_seconds)
        end = self.frame_idx(end_seconds)
        if end != len(self.__frames):
            self.__frames = self.__frames[start:end]
        return self

    def append(self, seconds):
        assert not self.locked
        required_extra = self.frame_idx(seconds)
        self.__frames += b"\0"*required_extra

    def mix(self, other, other_seconds=None, pad_shortest=True):
        assert not self.locked
        assert self.sampwidth == other.sampwidth
        assert self.samplerate == other.samplerate
        assert self.nchannels == other.nchannels
        frames1 = self.__frames
        if other_seconds:
            frames2 = other.__frames[:other.frame_idx(other_seconds)]
        else:
            frames2 = other.__frames
        if pad_shortest:
            if len(frames1) < len(frames2):
                frames1 += b"\0"*(len(frames2)-len(frames1))
            elif len(frames2) < len(frames1):
                frames2 += b"\0"*(len(frames1)-len(frames2))
        self.__frames = audioop.add(frames1, frames2, self.sampwidth)
        return self

    def mix_at(self, seconds, other, other_seconds=None):
        assert not self.locked
        assert self.sampwidth == other.sampwidth
        assert self.samplerate == other.samplerate
        assert self.nchannels == other.nchannels
        start_frame_idx = self.frame_idx(seconds)
        if other_seconds:
            other_frames = other.__frames[:other.frame_idx(other_seconds)]
        else:
            other_frames = other.__frames
        # Mix the frames. Unfortunately audioop requires splitting and copying the sample data, which is slow.
        pre, to_mix, post = self._mix_split_frames(len(other_frames), start_frame_idx)
        self.__frames = None  # allow for garbage collection
        mixed = audioop.add(to_mix, other_frames, self.sampwidth)
        del to_mix  # more garbage collection
        self.__frames = self._mix_join_frames(pre, mixed, post)
        return self

    def _mix_join_frames(self, pre, mid, post):     # XXX slow due to copying
        if post:
            return pre + mid + post
        elif mid:
            return pre + mid
        else:
            return pre

    def _mix_split_frames(self, other_frames_length, start_frame_idx):    # XXX slow due to copying
        self._mix_grow_if_needed(start_frame_idx, other_frames_length)
        pre = self.__frames[:start_frame_idx]
        to_mix = self.__frames[start_frame_idx:start_frame_idx + other_frames_length]
        post = self.__frames[start_frame_idx + other_frames_length:]
        return pre, to_mix, post

    def _mix_grow_if_needed(self, start_frame_idx, other_length):    # XXX slow due to copying
        required_length = start_frame_idx + other_length
        if required_length > len(self.__frames):
            # we need to extend the current sample buffer to make room for the mixed sample at the end
            self.__frames += b"\0" * (required_length - len(self.__frames))


class Mixer(object):
    """
    Mixes a set of ascii-bar tracks using the given sample instruments, into a resulting big sample.
    """
    def __init__(self, patterns, bpm, ticks, instruments):
        for p in patterns:
            bar_length = 0
            for instrument, bars in p.items():
                if instrument not in instruments:
                    raise ValueError("instrument '{:s}' not defined".format(instrument))
                if len(bars) % ticks != 0:
                    raise ValueError("bar length must be multiple of the number of ticks")
                if 0 < bar_length != len(bars):
                    raise ValueError("all bars must be of equal length in the same pattern")
                bar_length = len(bars)
        self.patterns = patterns
        self.instruments = instruments
        self.bpm = bpm
        self.ticks = ticks

    def mix(self, verbose=True):
        """
        Mix all the patterns into a single result sample.
        """
        if not self.patterns:
            if verbose:
                print("No patterns to mix, output is empty.")
            return Sample()
        total_seconds = 0.0
        for p in self.patterns:
            bar = next(iter(p.values()))
            total_seconds += len(bar) * 60.0 / self.bpm / self.ticks
        if verbose:
            print("Mixing {:d} patterns...".format(len(self.patterns)))
        mixed = Sample().make_32bit()
        for index, timestamp, sample in self.mixed_samples():
            if verbose:
                print("\r{:3.0f} % ".format(timestamp/total_seconds*100), end="")
            mixed.mix_at(timestamp, sample)
        # chop/extend to get to the precise total duration (in case of silence in the last bars etc)
        missing = total_seconds-mixed.duration
        if missing > 0:
            mixed.append(missing)
        elif missing < 0:
            mixed.cut(0, total_seconds)
        if verbose:
            print("\rMix done.")
        return mixed

    def mixed_triggers(self):
        """
        Generator for all triggers in chronological sequence.
        Every element is a tuple: (trigger index, time offset (seconds), list of (instrumentname, sample tuples)
        """
        time_per_index = 60.0 / self.bpm / self.ticks
        index = 0
        for num, pattern in enumerate(self.patterns, start=1):
            pattern = list(pattern.items())
            num_triggers = len(pattern[0][1])
            for i in range(num_triggers):
                triggers = []
                for instrument, bars in pattern:
                    if bars[i] not in ". ":
                        sample = self.instruments[instrument]
                        triggers.append((instrument, sample))
                if triggers:
                    yield index, time_per_index*index, triggers
                index += 1

    def mixed_samples(self):
        """
        Generator for all samples-to-mix.
        Every element is a tuple: (trigger index, time offset (seconds), sample)
        """
        mix_cache = {}  # we cache stuff to avoid repeated mixes of the same instruments
        for index, timestamp, triggers in self.mixed_triggers():
            if len(triggers) > 1:
                instruments_key = tuple(sorted(instrument for instrument, _ in triggers))
                if instruments_key in mix_cache:
                    yield index, timestamp, mix_cache[instruments_key]
                    continue
                # find the longest sample and create a copy of that to mix the others into
                longest_sample = max((s for _, s in triggers), key=lambda s: s.duration)
                mixed = longest_sample.dup()
                for instrument, sample in triggers:
                    if sample is longest_sample:
                        continue  # we started with this one, so don't mix it again
                    mixed.mix(sample)
                mixed.lock()
                mix_cache[instruments_key] = mixed   # cache the mixed instruments sample
                yield index, timestamp, mixed
            else:
                # simply yield the unmixed sample from the single trigger
                yield index, timestamp, triggers[0][1]


class Song(object):
    def __init__(self):
        self.instruments = {}
        self.sample_path = None
        self.output_path = None
        self.bpm = 128
        self.ticks = 4
        self.pattern_sequence = []
        self.patterns = {}

    def read(self, song_file, discard_unused_instruments=True):
        with open(song_file):
            pass    # test for file existence
        print("Loading song...")
        cp = ConfigParser()
        cp.read(song_file)
        self.sample_path = cp['paths']['samples']
        self.output_path = cp['paths']['output']
        self.read_samples(cp['instruments'], self.sample_path)
        if 'song' in cp:
            self.bpm = cp['song'].getint('bpm')
            self.ticks = cp['song'].getint('ticks')
            self.read_patterns(cp, cp['song']['patterns'].split())
        print("Done; {:d} instruments and {:d} patterns.".format(len(self.instruments), len(self.patterns)))
        unused_instruments = self.instruments.keys()
        for pattern_name in self.pattern_sequence:
            unused_instruments -= self.patterns[pattern_name].keys()
        if unused_instruments and discard_unused_instruments:
            for instrument in list(unused_instruments):
                del self.instruments[instrument]
            print("Warning: there are unused instruments. I've unloaded them from memory.")
            print("The unused instruments are:", ", ".join(sorted(unused_instruments)))

    def read_samples(self, instruments, samples_path):
        self.instruments = {}
        for name, file in sorted(instruments.items()):
            self.instruments[name] = Sample(wave_file=os.path.join(samples_path, file)).normalize().make_32bit(scale_amplitude=False).lock()

    def read_patterns(self, songdef, names):
        self.pattern_sequence = []
        self.patterns = {}
        for name in names:
            if "pattern."+name not in songdef:
                raise ValueError("pattern definition not found: "+name)
            bar_length = 0
            self.patterns[name] = {}
            for instrument, bars in songdef["pattern."+name].items():
                if instrument not in self.instruments:
                    raise ValueError("instrument '{instr:s}' not defined (pattern: {pattern:s})".format(instr=instrument, pattern=name))
                bars = bars.replace(' ', '')
                if len(bars) % self.ticks != 0:
                    raise ValueError("all patterns must be multiple of song ticks (pattern: {pattern:s}.{instr:s})".format(pattern=name, instr=instrument))
                self.patterns[name][instrument] = bars
                if 0 < bar_length != len(bars):
                    raise ValueError("all bars must be of equal length in the same pattern (pattern: {pattern:s}.{instr:s})".format(pattern=name, instr=instrument))
                bar_length = len(bars)
            self.pattern_sequence.append(name)

    def write(self, output_filename):
        import collections
        cp = ConfigParser(dict_type=collections.OrderedDict)
        cp["paths"] = {"samples": self.sample_path, "output": self.output_path}
        cp["song"] = {"bpm": self.bpm, "ticks": self.ticks, "patterns": " ".join(self.pattern_sequence)}
        cp["instruments"] = {}
        for name, sample in sorted(self.instruments.items()):
            cp["instruments"][name] = os.path.basename(sample.filename)
        for name, pattern in sorted(self.patterns.items()):
            # Note: the layout of the patterns is not optimized for human viewing. You may want to edit it afterwards.
            cp["pattern."+name] = collections.OrderedDict(sorted(pattern.items()))
        with open(output_filename, 'w') as f:
            cp.write(f)
        print("Saved to '{:s}'.".format(output_filename))

    def mix(self, output_filename):
        if not self.output_path or not self.pattern_sequence:
            raise ValueError("There's nothing to be mixed; no song loaded or song has no patterns.")
        patterns = [self.patterns[name] for name in self.pattern_sequence]
        mixer = Mixer(patterns, self.bpm, self.ticks, self.instruments)
        result = mixer.mix()
        output_filename = os.path.join(self.output_path, output_filename)
        result.make_16bit()
        result.write_wav(output_filename)
        print("Output is {:.2f} seconds, written to: {:s}".format(result.duration, output_filename))
        return result

    def mixed_triggers(self):
        patterns = [self.patterns[name] for name in self.pattern_sequence]
        mixer = Mixer(patterns, self.bpm, self.ticks, self.instruments)
        yield from mixer.mixed_triggers()


class Repl(cmd.Cmd):
    """
    Interactive command line interface to load/record/save and play samples, patterns and whole tracks.
    Currently it has no way of defining and loading samples manually. This means you need to initialize
    it with a track file containing at least the instruments (samples) that you will be using.
    """
    def __init__(self, discard_unused_instruments=False):
        self.song = Song()
        self.discard_unused_instruments = discard_unused_instruments
        if pyaudio:
            self.audio = pyaudio.PyAudio()
        else:
            self.audio = None
        super(Repl, self).__init__()

    def do_quit(self, args):
        """quits the session"""
        print("Bye.", args)
        if self.audio:
            self.audio.terminate()
        return True

    def do_bpm(self, bpm):
        """set the playback BPM (such as 174 for some drum'n'bass)"""
        try:
            self.song.bpm = int(bpm)
        except ValueError as x:
            print("ERROR:", x)

    def do_ticks(self, ticks):
        """set the number of pattern ticks per beat (usually 4 or 8)"""
        try:
            self.song.ticks = int(ticks)
        except ValueError as x:
            print("ERROR:", x)

    def do_samples(self, args):
        """show the loaded samples"""
        print("Samples:")
        print(",  ".join(self.song.instruments))

    def do_patterns(self, args):
        """show the loaded patterns"""
        print("Patterns:")
        for name, pattern in sorted(self.song.patterns.items()):
            self.print_pattern(name, pattern)

    def print_pattern(self, name, pattern):
        print("PATTERN {:s}".format(name))
        for instrument, bars in pattern.items():
            print("   {:>15s} = {:s}".format(instrument, bars))

    def do_pattern(self, names):
        """play the pattern with the given name(s)"""
        names = names.split()
        for name in sorted(set(names)):
            try:
                pat = self.song.patterns[name]
                self.print_pattern(name, pat)
            except KeyError:
                print("no such pattern '{:s}'".format(name))
                return
        patterns = [self.song.patterns[name] for name in names]
        try:
            m = Mixer(patterns, self.song.bpm, self.song.ticks, self.song.instruments)
            result = m.mix(verbose=len(patterns) > 1)
            self.play_sample(result)
        except ValueError as x:
            print("ERROR:", x)

    def do_play(self, args):
        """play a single sample by giving its name, add a bar (xx..x.. etc) to play it in a bar"""
        if ' ' in args:
            instrument, pattern = args.split(maxsplit=1)
            pattern = pattern.replace(' ', '')
        else:
            instrument = args
            pattern = None
        instrument = instrument.strip()
        try:
            sample = self.song.instruments[instrument]
        except KeyError:
            print("unknown sample")
            return
        if pattern:
            self.play_single_bar(sample, pattern)
        else:
            self.play_sample(sample)

    def play_sample(self, sample):
        if sample.sampwidth not in (2, 3):
            sample = sample.dup().make_16bit()
        if self.audio:
            with contextlib.closing(self.audio.open(
                    format=self.audio.get_format_from_width(sample.sampwidth),
                    channels=sample.nchannels, rate=sample.samplerate, output=True)) as stream:
                sample.write_frames(stream)
                time.sleep(stream.get_output_latency()+stream.get_input_latency()+0.001)
        else:
            # try to fallback to winsound (only works on windows)
            sample_file = "__temp_sample.wav"
            sample.write_wav(sample_file)
            winsound.PlaySound(sample_file, winsound.SND_FILENAME)
            os.remove(sample_file)

    def play_single_bar(self, sample, pattern):
        try:
            m = Mixer([{"sample": pattern}], self.song.bpm, self.song.ticks, {"sample": sample})
            result = m.mix(verbose=False)
            self.play_sample(result)
        except ValueError as x:
            print("ERROR:", x)

    def do_mix(self, args):
        """mix and play all patterns of the song"""
        if not self.song.pattern_sequence:
            print("Nothing to be mixed.")
            return
        output = "__temp_mix.wav"
        self.song.mix(output)
        mix = Sample(wave_file=output)
        print("Playing sound...")
        self.play_sample(mix)
        os.remove(output)

    def do_rec(self, args):
        """Record (or overwrite) a new sample (instrument) bar in a pattern.
Args: [pattern name] [sample] [bar(s)].
Omit bars to remove the sample from the pattern.
If a pattern with the name doesn't exist yet it will be added."""
        args = args.split(maxsplit=2)
        if len(args) not in (2, 3):
            print("Wrong arguments. Use: patternname sample bar(s)")
            return
        if len(args) == 2:
            args.append(None)   # no bars
        pattern_name, instrument, bars = args
        if instrument not in self.song.instruments:
            print("Unknown sample '{:s}'.".format(instrument))
            return
        if pattern_name not in self.song.patterns:
            self.song.patterns[pattern_name] = {}
        pattern = self.song.patterns[pattern_name]
        if bars:
            bars = bars.replace(' ', '')
            if len(bars) % self.song.ticks != 0:
                print("Bar length must be multiple of the number of ticks.")
                return
            pattern[instrument] = bars
        else:
            if instrument in pattern:
                del pattern[instrument]
        if pattern_name in self.song.patterns:
            if not self.song.patterns[pattern_name]:
                del self.song.patterns[pattern_name]
                print("Pattern was empty and has been removed.")
            else:
                self.print_pattern(pattern_name, self.song.patterns[pattern_name])

    def do_seq(self, names):
        """Print the sequence of patterns that form the current track, or if you give a list of names: use that as the new pattern sequence."""
        if not names:
            print("  ".join(self.song.pattern_sequence))
            return
        names = names.split()
        for name in names:
            if name not in self.song.patterns:
                print("Unknown pattern '{:s}'.".format(name))
                return
        self.song.pattern_sequence = names

    def do_load(self, filename):
        """Load a new song file"""
        song = Song()
        try:
            song.read(filename, self.discard_unused_instruments)
            self.song = song
        except IOError as x:
            print("ERROR:", x)

    def do_save(self, filename):
        """Save current song to file"""
        if not filename:
            print("Give filename to save song to.")
            return
        if not filename.endswith(".ini"):
            filename += ".ini"
        if os.path.exists(filename):
            if input("File exists: '{:s}'. Overwrite y/n? ".format(filename)) not in ('y', 'yes'):
                return
        self.song.write(filename)


def main(track_file, outputfile=None, interactive=False):
    discard_unused = not interactive
    if interactive:
        repl = Repl(discard_unused_instruments=discard_unused)
        repl.do_load(track_file)
        repl.cmdloop("Interactive Samplebox session. Type 'help' for help on commands.")
    else:
        song = Song()
        song.read(track_file, discard_unused_instruments=discard_unused)
        song.mix(outputfile)
        mix = Sample(wave_file=outputfile)
        # XXX print("Playing sound...")
        # XXX Repl().play_sample(mix)


def usage():
    print("Arguments:  [-i] trackfile.ini")
    print("   -i = start interactive editing mode")
    raise SystemExit(1)

if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        usage()
    track_file = None
    interactive = False
    if len(sys.argv) == 2:
        if sys.argv[1] == "-i":
            usage()  # need a trackfile as well to at least initialize the samples
        else:
            track_file = sys.argv[1]
    elif len(sys.argv) == 3:
        if sys.argv[1] != "-i":
            usage()
        interactive = True
        track_file = sys.argv[2]
    if interactive:
        main(track_file, interactive=True)
    else:
        output_file = os.path.splitext(track_file)[0]+".wav"
        main(track_file, output_file)