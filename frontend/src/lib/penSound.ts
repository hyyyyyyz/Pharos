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
/** The confirmation beeps. Louder than the nib on purpose: this one has to be
 *  heard once, over a room, to answer a yes/no question. */
const TEST_GAIN = 0.35;
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
  /** The last level `nib()` asked for, surfaced in the toolbar readout so a
   *  reader who hears nothing can say whether the graph was even trying. */
  private lastVoice = 0;
  private filter: BiquadFilterNode | null = null;
  private source: AudioBufferSourceNode | null = null;
  private limiter: DynamicsCompressorNode | null = null;
  private failed = false;

  /**
   * Build the graph, from inside a real user gesture.
   *
   * Call this from a **click** or a **pointerUP** — never from pointerdown or
   * pointermove, and that distinction is the whole bug.
   *
   * `nib()` is driven by `pointermove`, which is not a user-activation trigger
   * anywhere, so a context built there starts `suspended` and the `resume()`
   * that follows is refused for the same reason. Round 4 moved the arming to
   * `pointerdown`, which fixed it on a desktop and changed nothing on the
   * tablet — because per HTML's "activation triggering input event" list,
   * `pointerdown` only counts **when `pointerType` is "mouse"**. For a pen or
   * a finger the activating event is `pointerup`. So the desktop test passed
   * with a mouse while an S Pen armed the graph with no activation at all,
   * built it suspended, and wrote in silence: "还是没有写字音效".
   *
   * Still lazy: a reader who never turns the sound on never pays for an audio
   * device at all.
   */
  arm(): void {
    this.ensure();
  }

  /**
   * What the audio device is actually doing, for the on-screen readout.
   *
   * Shipped diagnostics, same reasoning as 笔尖诊断: this feature has now
   * failed twice on hardware none of this code can be run against, and
   * "running" versus "suspended" is the one fact that separates a browser
   * policy problem from a volume problem. Without it the next report is
   * another "还是没有".
   */
  state(): "未启动" | "运行中" | "已挂起" | "不可用" {
    if (this.failed) return "不可用";
    if (!this.ctx) return "未启动";
    return this.ctx.state === "running" ? "运行中" : "已挂起";
  }

  /**
   * A one-line report for the toolbar: is the device running, and what level
   * did the nib last ask for?
   *
   * The level is the half that was missing. "运行中" alone says the graph is
   * alive but not whether it is producing anything, so a reader could still
   * only report "no sound" — the same dead end as the last three rounds. A
   * number they can read out turns "no sound" into either "the graph is
   * asking for 0.00" (the app's fault) or "asking for 0.19 and I hear
   * nothing" (the device's).
   */
  report(): string {
    const s = this.state();
    if (s !== "运行中") return s;
    return `运行中 · 音量 ${this.lastVoice.toFixed(2)}`;
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
      let peak = 0;
      for (let i = 0; i < frames; i++) {
        const white = Math.random() * 2 - 1;
        prev = prev * 0.6 + white * 0.4;
        data[i] = prev;
        const mag = prev < 0 ? -prev : prev;
        if (mag > peak) peak = mag;
      }
      // NORMALISE. The one-pole filter above is where most of the missing
      // loudness went: averaging ±1 white noise leaves an RMS of about 0.29,
      // roughly 11 dB below full scale, before anything downstream has taken
      // its own cut. Three rounds of raising `PEAK_GAIN` were compensating at
      // the wrong end of the chain for a signal that arrived quiet.
      if (peak > 0) {
        const norm = 0.95 / peak;
        for (let i = 0; i < frames; i++) data[i] *= norm;
      }

      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.loop = true;

      // A gentle BANDSHELF rather than a narrow bandpass. `bandpass` at
      // Q 0.7 threw away roughly another 5 dB of a source that had already
      // been low-passed toward its own passband — paying twice for the same
      // timbre. A peaking filter keeps the paper character (a lift around the
      // nib's own frequencies) while passing the rest of the signal at unity,
      // so the level that reaches the gain node is the level the buffer has.
      const filter = ctx.createBiquadFilter();
      filter.type = "peaking";
      filter.frequency.value = 1600;
      filter.Q.value = 0.9;
      filter.gain.value = 6;

      const gain = ctx.createGain();
      gain.gain.value = 0;

      // A limiter on the way out. With the source at full scale the peaks are
      // real peaks, and a nib that clips sounds like a fault rather than like
      // paper; this catches them without costing average level.
      const limiter = ctx.createDynamicsCompressor();
      limiter.threshold.value = -6;
      limiter.knee.value = 6;
      limiter.ratio.value = 12;
      limiter.attack.value = 0.003;
      limiter.release.value = 0.12;

      source.connect(filter).connect(gain).connect(limiter).connect(ctx.destination);
      source.start();

      this.ctx = ctx;
      this.source = source;
      this.filter = filter;
      this.gain = gain;
      this.limiter = limiter;
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
    this.lastVoice = PEAK_GAIN * voice;
    this.gain.gain.cancelScheduledValues(now);
    this.gain.gain.setTargetAtTime(PEAK_GAIN * voice, now, ATTACK);
    this.filter.frequency.setTargetAtTime(900 + 1800 * t, now, ATTACK);
  }

  /**
   * Two short beeps, now — what the 音效 toggle plays when it is switched ON.
   *
   * A clear TONE rather than the nib's filtered noise, and deliberately: a
   * faint scratch on a tablet, in a room, with a hand moving across the glass,
   * is exactly the sound a reader cannot tell from silence. A pair of sine
   * beeps cannot be mistaken for anything, so "did that work?" gets an answer
   * at the moment the question is asked instead of later, under a pen.
   *
   * It also runs from a click, which IS an activation-triggering event — so
   * this is what actually gets the context into `running` on a tablet, where
   * a stylus pointerdown never would.
   */
  test(): void {
    if (!this.ensure() || !this.ctx) return;
    const ctx = this.ctx;
    const now = ctx.currentTime;
    const beep = (at: number, hz: number): void => {
      const osc = ctx.createOscillator();
      const g = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = hz;
      g.gain.setValueAtTime(0, now + at);
      g.gain.linearRampToValueAtTime(TEST_GAIN, now + at + 0.02);
      g.gain.linearRampToValueAtTime(0, now + at + 0.13);
      osc.connect(g).connect(this.limiter ?? ctx.destination);
      osc.start(now + at);
      osc.stop(now + at + 0.16);
    };
    beep(0, 660);
    beep(0.17, 880);
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
    this.limiter = null;
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
