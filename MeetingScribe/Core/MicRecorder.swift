import Foundation
import AVFoundation

// captures mic audio via AVAudioEngine, outputs chunked WAV files
class MicRecorder {
    private let chunkSeconds: Int
    private let outputDir: URL
    private let onChunk: (URL, TimeInterval) -> Void

    private var engine: AVAudioEngine?
    private var chunkBuffer = Data()
    private var elapsedSeconds: TimeInterval = 0
    private let bufferLock = NSLock()

    // match AudioRecorder's format
    private static let sampleRate: Double = 16000
    private static let bytesPerSample = 2

    init(chunkSeconds: Int = 30, outputDir: URL, onChunk: @escaping (URL, TimeInterval) -> Void) {
        self.chunkSeconds = chunkSeconds
        self.outputDir = outputDir
        self.onChunk = onChunk
    }

    func start() throws {
        let engine = AVAudioEngine()
        let input = engine.inputNode
        let nativeFormat = input.outputFormat(forBus: 0)

        // target format: 16kHz mono float32 (converted to int16 in tap)
        guard let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: Self.sampleRate,
            channels: 1,
            interleaved: false
        ) else {
            throw MicRecorderError.formatError
        }

        guard let converter = AVAudioConverter(from: nativeFormat, to: targetFormat) else {
            throw MicRecorderError.converterError
        }

        let bytesPerChunk = Int(Self.sampleRate) * chunkSeconds * Self.bytesPerSample

        input.installTap(onBus: 0, bufferSize: 4096, format: nativeFormat) { [weak self] buffer, _ in
            guard let self else { return }

            // convert to 16kHz mono
            let frameCount = AVAudioFrameCount(
                Double(buffer.frameLength) * Self.sampleRate / nativeFormat.sampleRate
            )
            guard frameCount > 0,
                  let converted = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: frameCount) else { return }

            var error: NSError?
            converter.convert(to: converted, error: &error) { _, outStatus in
                outStatus.pointee = .haveData
                return buffer
            }

            if error != nil { return }

            // convert float32 -> int16 pcm
            let pcm = Self.float32ToInt16(converted)

            self.bufferLock.lock()
            self.chunkBuffer.append(pcm)

            if self.chunkBuffer.count >= bytesPerChunk {
                let chunk = self.chunkBuffer.prefix(bytesPerChunk)
                self.chunkBuffer = Data(self.chunkBuffer.dropFirst(bytesPerChunk))
                let offset = self.elapsedSeconds
                self.elapsedSeconds += Double(bytesPerChunk / Self.bytesPerSample) / Self.sampleRate
                self.bufferLock.unlock()

                if !Self.isSilent(chunk) {
                    if let url = self.writeWAV(Data(chunk), sampleRate: Int(Self.sampleRate)) {
                        self.onChunk(url, offset)
                    }
                }
            } else {
                self.bufferLock.unlock()
            }
        }

        try engine.start()
        self.engine = engine
        print("[MicRecorder] started")
    }

    /// stop recording and return any remaining buffered audio as a WAV file
    func stop() -> (url: URL, offset: TimeInterval)? {
        engine?.inputNode.removeTap(onBus: 0)
        engine?.stop()
        engine = nil

        bufferLock.lock()
        let remaining = chunkBuffer
        let offset = elapsedSeconds
        chunkBuffer = Data()
        bufferLock.unlock()

        print("[MicRecorder] stopped")

        if remaining.count >= Self.bytesPerSample && !Self.isSilent(remaining) {
            if let url = self.writeWAV(remaining, sampleRate: Int(Self.sampleRate)) {
                return (url, offset)
            }
        }
        return nil
    }

    // MARK: - private

    private static func float32ToInt16(_ buffer: AVAudioPCMBuffer) -> Data {
        guard let floats = buffer.floatChannelData?[0] else { return Data() }
        let count = Int(buffer.frameLength)
        var data = Data(capacity: count * bytesPerSample)
        for i in 0..<count {
            let clamped = max(-1.0, min(1.0, floats[i]))
            var sample = Int16(clamped * 32767)
            withUnsafeBytes(of: &sample) { data.append(contentsOf: $0) }
        }
        return data
    }

    private static func isSilent(_ data: Data) -> Bool {
        data.withUnsafeBytes { raw in
            let samples = raw.bindMemory(to: Int16.self)
            var sum: Float = 0
            for s in samples { sum += abs(Float(s) / 32768.0) }
            let mean = sum / Float(max(samples.count, 1))
            return mean < AudioRecorder.silenceThreshold
        }
    }

    private func writeWAV(_ pcmData: Data, sampleRate: Int) -> URL? {
        let dir = outputDir.appendingPathComponent("mic")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let filename = String(format: "mic_%.1f.wav", elapsedSeconds)
        let url = dir.appendingPathComponent(filename)

        var header = Data()
        let dataSize = UInt32(pcmData.count)
        let fileSize = dataSize + 36

        header.append(contentsOf: "RIFF".utf8)
        header.append(withUnsafeBytes(of: fileSize.littleEndian) { Data($0) })
        header.append(contentsOf: "WAVE".utf8)
        header.append(contentsOf: "fmt ".utf8)
        header.append(withUnsafeBytes(of: UInt32(16).littleEndian) { Data($0) })
        header.append(withUnsafeBytes(of: UInt16(1).littleEndian) { Data($0) })   // PCM
        header.append(withUnsafeBytes(of: UInt16(1).littleEndian) { Data($0) })   // mono
        header.append(withUnsafeBytes(of: UInt32(sampleRate).littleEndian) { Data($0) })
        header.append(withUnsafeBytes(of: UInt32(sampleRate * Self.bytesPerSample).littleEndian) { Data($0) })
        header.append(withUnsafeBytes(of: UInt16(Self.bytesPerSample).littleEndian) { Data($0) })
        header.append(withUnsafeBytes(of: UInt16(16).littleEndian) { Data($0) })
        header.append(contentsOf: "data".utf8)
        header.append(withUnsafeBytes(of: dataSize.littleEndian) { Data($0) })

        var wavData = header
        wavData.append(pcmData)

        do {
            try wavData.write(to: url)
            return url
        } catch {
            print("[MicRecorder] failed to write wav: \(error)")
            return nil
        }
    }
}

enum MicRecorderError: LocalizedError {
    case formatError
    case converterError

    var errorDescription: String? {
        switch self {
        case .formatError: return "Failed to create target audio format"
        case .converterError: return "Failed to create audio converter"
        }
    }
}
