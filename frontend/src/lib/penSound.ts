/**
 * The sound of a nib on paper — 书写音效.
 *
 * Synthesised, not sampled: filtered noise whose loudness and brightness
 * follow how fast the pen is actually moving. A recorded scratch loop would
 * mean shipping an audio asset, would repeat audibly on a long stroke, and
 * could not respond to speed at all — the thing that makes it feel connected
 * to the writing rather than played at it.
 *
 * The chain, once per document:
 *
 *     noise (looping buffer) → bandpass → gain → destination
 *
 * `nib()` is called with the pen's speed on every input batch and moves the
 * gain and the filter frequency toward it; `lift()` fades out. Everything is
 * ramped rather than set, because an abrupt gain change on a noise source is
 * a click.
 *
 * OFF by default, on purpose. A reader annotating a paper in a library or a
 * shared office does not want a surprise noise from a page, and a feature
 * that has to be discovered to be turned OFF is a worse default than one that
 * has to be discovered to be turned on.
 */

/** Loudest the nib ever gets. Deliberately faint: this is a texture under the
 *  writing, not a sound effect competing with it. */
const PEAK_GAIN = 0.055;
/** Speed (CSS px per ms) at which the nib reaches full voice. A brisk hand is
 *  around 1.5-2; anything past this is already as loud as it gets. */
const FULL_SPEED = 1.8;
/** Ramp constants, in seconds. Short enough to track the hand, long enough
 *  that neither the gain nor the filter sweep ever clicks. */
const ATTACK = 0.02;
const RELEASE = 0.09;

type AudioCtor = typeof AudioContext;

export class PenSound {
  private ctx: AudioContext | null = null;
  private gain: GainNode | null = null;
  private filter: BiquadFilterNode | null = null;
  private source: AudioBufferSourceNode | null = null;
  private failed = false;

  /**
   * Build the graph on first use.
   *
   * Lazily, because an AudioContext created before a user gesture starts
   * suspended in every current browser, and because a reader who never turns
   * the sound on should never pay for one at all. Called from a pointerdown,
   * which IS the gesture that allows it to start.
   */
  private ensure(): boolean {
    if (this.failed) return false;
    if (this.ctx) {
      // Autoplay policy can still have parked it (a tab restored in the
      // background, say); asking again is harmless when it is already running.
      if (this.ctx.state === "suspended") void this.ctx.resume().catch(() => undefined);
      return true;
    }
    try {
      const Ctor: AudioCtor | undefined =
        window.AudioContext ??
        (window as unknown as { webkitAudioContext?: AudioCtor }).webkitAudioContext;
      if (!Ctor) {
        this.failed = true;
        return false;
      }
      const ctx = new Ctor();

      // Two seconds of noise, looped. Long enough that the loop point is not
      // audible as a rhythm the way a short buffer would be.
      const frames = Math.floor(ctx.sampleRate * 2);
      const buffer = ctx.createBuffer(1, frames, ctx.sampleRate);
      const data = buffer.getChannelData(0);
      // Slightly low-passed noise (a one-pole average) rather than white:
      // paper texture has more energy low down, and pure white noise reads as
      // hiss/static.
      let prev = 0;
      for (let i = 0; i < frames; i++) {
        const white = Math.random() * 2 - 1;
        prev = prev * 0.6 + white * 0.4;
        data[i] = prev;
      }

      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.loop = true;

      const filter = ctx.createBiquadFilter();
      filter.type = "bandpass";
      filter.frequency.value = 1400;
      filter.Q.value = 0.7;

      const gain = ctx.createGain();
      gain.gain.value = 0;

      source.connect(filter).connect(gain).connect(ctx.destination);
      source.start();

      this.ctx = ctx;
      this.source = source;
      this.filter = filter;
      this.gain = gain;
      return true;
    } catch {
      // No audio device, a policy refusal, a locked-down WebView: writing must
      // carry on regardless, so this is remembered and never retried.
      this.failed = true;
      return false;
    }
  }

  /**
   * The pen is on the paper and moving at `speed` (CSS px per ms).
   *
   * Faster writing is both louder and brighter, which is what a nib actually
   * does; a stationary pen is silent rather than droning.
   */
  nib(speed: number): void {
    if (!this.ensure() || !this.ctx || !this.gain || !this.filter) return;
    const t = Math.max(0, Math.min(1, speed / FULL_SPEED));
    const now = this.ctx.currentTime;
    // sqrt so that slow, deliberate writing is still audible — a linear map
    // leaves careful handwriting almost silent.
    this.gain.gain.cancelScheduledValues(now);
    this.gain.gain.setTargetAtTime(PEAK_GAIN * Math.sqrt(t), now, ATTACK);
    this.filter.frequency.setTargetAtTime(900 + 1800 * t, now, ATTACK);
  }

  /** The pen has left the paper. */
  lift(): void {
    if (!this.ctx || !this.gain) return;
    const now = this.ctx.currentTime;
    this.gain.gain.cancelScheduledValues(now);
    this.gain.gain.setTargetAtTime(0, now, RELEASE);
  }

  /** Release the device — the reader turned the sound off, or left the page. */
  dispose(): void {
    this.lift();
    try {
      this.source?.stop();
      void this.ctx?.close();
    } catch {
      /* already gone */
    }
    this.ctx = null;
    this.gain = null;
    this.filter = null;
    this.source = null;
  }
}

/** One graph per tab: a second AudioContext per page component would be a
 *  second noise source playing over the first. */
let shared: PenSound | null = null;

export function penSound(): PenSound {
  shared ??= new PenSound();
  return shared;
}
