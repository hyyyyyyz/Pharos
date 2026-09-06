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

/**
 * Loudest the nib ever gets.
 *
 * The first version used 0.055 and called it "deliberately faint". On a
 * tablet that is not faint, it is **silent** — "音效失败，没有出现音效". The
 * arithmetic nobody did: the noise source runs at RMS ≈ 0.3 after its
 * one-pole filter, a Q=0.7 bandpass throws away roughly half of what is left,
 * and the speed mapping then multiplied by another 0.4 for the unhurried pace
 * handwriting actually goes at. 0.055 × 0.3 × 0.5 × 0.4 ≈ 0.0033 — about
 * -50 dBFS, under the noise floor of a room, never mind a tablet speaker with
 * a hand moving across the glass in front of it.
 */
const PEAK_GAIN = 0.22;
/**
 * Loudness at the slowest audible movement, as a fraction of `PEAK_GAIN`.
 *
 * A nib in contact and moving at all makes a sound; only a stationary pen is
 * silent. Without a floor, careful handwriting — which is most handwriting —
 * sits at the bottom of the speed curve and is inaudible however loud the
 * peak is.
 */
const MIN_VOICE = 0.35;
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
   * Build the graph, from inside a real user gesture.
   *
   * This must be called from a **pointerdown**, and that is the second half of
   * why the sound never arrived: `nib()` is driven by `pointermove`, which is
   * not a user-activation trigger in any browser. An AudioContext constructed
   * there starts `suspended`, and the `resume()` that follows is refused for
   * exactly the same reason — so the graph was built, connected, and mute.
   *
   * Still lazy: a reader who never turns the sound on never pays for an audio
   * device at all.
   */
  arm(): void {
    this.ensure();
  }

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
    // leaves careful handwriting almost silent — and a floor under that, so
    // "audible" does not depend on writing fast.
    const voice = MIN_VOICE + (1 - MIN_VOICE) * Math.sqrt(t);
    this.gain.gain.cancelScheduledValues(now);
    this.gain.gain.setTargetAtTime(PEAK_GAIN * voice, now, ATTACK);
    this.filter.frequency.setTargetAtTime(900 + 1800 * t, now, ATTACK);
  }

  /**
   * One short scratch, now — the sound of a nib touching down and lifting.
   *
   * What the 音效 toggle plays when it is switched ON. A setting whose only
   * feedback arrives later, under a pen, on a device whose media volume might
   * be at zero, is a setting nobody can tell is broken; this answers "did that
   * work?" at the moment the question is asked. It is also the click that
   * *creates* the AudioContext inside a real gesture, so the first stroke
   * afterward already has a running graph.
   */
  test(): void {
    if (!this.ensure() || !this.ctx || !this.gain || !this.filter) return;
    const now = this.ctx.currentTime;
    this.gain.gain.cancelScheduledValues(now);
    this.gain.gain.setValueAtTime(0, now);
    this.gain.gain.linearRampToValueAtTime(PEAK_GAIN, now + 0.03);
    this.gain.gain.linearRampToValueAtTime(0, now + 0.22);
    this.filter.frequency.setValueAtTime(1600, now);
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
