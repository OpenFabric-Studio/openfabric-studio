/** Test double for the Web Audio boundary; tracks connections and scheduled events.
 * It does not simulate DSP or claim browser audio-rendering fidelity. */
export class TestAudioParam {
  value = 0
  events: { kind: 'set' | 'ramp'; value: number; time: number }[] = []

  setValueAtTime(value: number, time: number): void {
    this.events.push({ kind: 'set', value, time })
  }

  linearRampToValueAtTime(value: number, time: number): void {
    this.events.push({ kind: 'ramp', value, time })
  }
}

export class TestAudioNode {
  readonly kind: string
  connections = new Set<TestAudioNode | TestAudioParam>()
  disconnectCalls = 0
  startCalls: number[][] = []
  stopCalls: number[] = []
  onended: (() => void) | null = null
  type = 'sine'
  gain = new TestAudioParam()
  frequency = new TestAudioParam()
  Q = new TestAudioParam()
  threshold = new TestAudioParam()
  ratio = new TestAudioParam()
  knee = new TestAudioParam()
  attack = new TestAudioParam()
  release = new TestAudioParam()
  pan = new TestAudioParam()
  delayTime = new TestAudioParam()
  playbackRate = new TestAudioParam()
  buffer: AudioBuffer | null = null
  curve: Float32Array<ArrayBuffer> | null = null
  fftSize = 256
  smoothingTimeConstant = 0

  constructor(kind: string) {
    this.kind = kind
  }

  connect(destination: TestAudioNode | TestAudioParam): void {
    this.connections.add(destination)
  }

  disconnect(): void {
    this.disconnectCalls++
    this.connections.clear()
  }

  start(...args: number[]): void {
    this.startCalls.push(args)
  }

  stop(time = 0): void {
    this.stopCalls.push(time)
  }

  getFloatTimeDomainData(data: Float32Array): void {
    data.fill(0)
  }
}

export class TestAudioBuffer {
  readonly numberOfChannels: number
  readonly length: number
  readonly sampleRate: number
  readonly duration: number
  private readonly channels: Float32Array<ArrayBuffer>[]

  constructor(options: AudioBufferOptions) {
    this.numberOfChannels = options.numberOfChannels ?? 1
    this.length = options.length
    this.sampleRate = options.sampleRate
    this.duration = options.length / options.sampleRate
    this.channels = Array.from({ length: this.numberOfChannels }, () => new Float32Array(this.length))
  }

  getChannelData(channel: number): Float32Array<ArrayBuffer> {
    const data = this.channels[channel]
    if (!data) throw new RangeError('Channel out of range')
    return data
  }

  copyToChannel(source: Float32Array, channel: number): void {
    this.getChannelData(channel).set(source)
  }
}

export class TestAudioContext {
  static instances: TestAudioContext[] = []
  readonly nodes: TestAudioNode[] = []
  readonly destination = this.node('destination')
  readonly sampleRate = 8000
  currentTime = 5
  resumeResult: Promise<void> = Promise.resolve()

  constructor() {
    TestAudioContext.instances.push(this)
  }

  protected node(kind: string): TestAudioNode {
    const node = new TestAudioNode(kind)
    this.nodes.push(node)
    return node
  }

  createGain(): TestAudioNode { return this.node('gain') }
  createBiquadFilter(): TestAudioNode { return this.node('filter') }
  createWaveShaper(): TestAudioNode { return this.node('shaper') }
  createDelay(): TestAudioNode { return this.node('delay') }
  createOscillator(): TestAudioNode { return this.node('oscillator') }
  createDynamicsCompressor(): TestAudioNode { return this.node('compressor') }
  createStereoPanner(): TestAudioNode { return this.node('panner') }
  createConvolver(): TestAudioNode { return this.node('convolver') }
  createAnalyser(): TestAudioNode { return this.node('analyser') }
  createChannelSplitter(): TestAudioNode { return this.node('splitter') }
  createBufferSource(): TestAudioNode { return this.node('buffer-source') }

  resume(): Promise<void> { return this.resumeResult }
}

export class TestOfflineAudioContext extends TestAudioContext {
  static renderError: Error | null = null
  readonly channelCount: number
  readonly frameCount: number
  readonly renderSampleRate: number

  constructor(channelCount: number, frameCount: number, renderSampleRate: number) {
    super()
    this.channelCount = channelCount
    this.frameCount = frameCount
    this.renderSampleRate = renderSampleRate
  }

  createBuffer(numberOfChannels: number, length: number, sampleRate: number): AudioBuffer {
    return new AudioBuffer({ numberOfChannels, length, sampleRate })
  }

  async startRendering(): Promise<AudioBuffer> {
    if (TestOfflineAudioContext.renderError) throw TestOfflineAudioContext.renderError
    return this.createBuffer(this.channelCount, this.frameCount, this.renderSampleRate)
  }
}
