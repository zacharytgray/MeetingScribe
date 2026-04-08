import Foundation

// manages audiotee subprocess + FIFO for system audio capture
class AudioRecorder {
    // pcm format: 16-bit signed LE, 16kHz mono
    static let sampleRate: Int = 16000
    static let bytesPerSample: Int = 2
    static let blockDuration: Double = 0.2  // 200ms
    static let blockBytes: Int = Int(Double(sampleRate) * blockDuration) * bytesPerSample  // 6400
    static let silenceThreshold: Float = 0.001

    private let chunkSeconds: Int
    private let outputDir: URL
    private let onChunk: (URL, TimeInterval) -> Void  // (wav path, chunk start offset)

    private var childPID: pid_t = -1
    private var readFD: Int32 = -1
    private var readThread: Thread?
    private var running = false
    private var elapsedSeconds: TimeInterval = 0
    private let threadDone = DispatchSemaphore(value: 0)

    // flush state — written by read thread, read by stop()
    private var flushData: Data?
    private var flushOffset: TimeInterval = 0

    // state file paths
    private static let stateDir = NSHomeDirectory() + "/.meetingscribe"
    private static let fifoPath = stateDir + "/audiotee.fifo"
    private static let pidPath = stateDir + "/audiotee.pid"

    init(chunkSeconds: Int = 30, outputDir: URL, onChunk: @escaping (URL, TimeInterval) -> Void) {
        self.chunkSeconds = chunkSeconds
        self.outputDir = outputDir
        self.onChunk = onChunk
    }

    // MARK: - public

    static var isAudioteeInstalled: Bool {
        audioteePath != nil
    }

    static var audioteePath: String? {
        let candidates = [
            NSHomeDirectory() + "/.local/bin/audiotee",
            "/usr/local/bin/audiotee",
            "/opt/homebrew/bin/audiotee"
        ]
        for c in candidates {
            if FileManager.default.isExecutableFile(atPath: c) { return c }
        }
        // try which
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/which")
        task.arguments = ["audiotee"]
        let pipe = Pipe()
        task.standardOutput = pipe
        task.standardError = FileHandle.nullDevice
        try? task.run()
        task.waitUntilExit()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let path = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let path, !path.isEmpty, FileManager.default.isExecutableFile(atPath: path) {
            return path
        }
        return nil
    }

    func start() throws {
        guard let binary = Self.audioteePath else {
            throw AudioRecorderError.audioteeNotFound
        }

        Self.cleanupOrphans()

        let fm = FileManager.default
        try fm.createDirectory(atPath: Self.stateDir, withIntermediateDirectories: true)

        // create fifo
        let fifo = Self.fifoPath
        if fm.fileExists(atPath: fifo) { try? fm.removeItem(atPath: fifo) }
        guard mkfifo(fifo, 0o600) == 0 else {
            throw AudioRecorderError.fifoCreationFailed
        }

        // open fifo O_RDWR (posix trick: won't block)
        let bootstrapFD = open(fifo, O_RDWR)
        guard bootstrapFD >= 0 else {
            throw AudioRecorderError.fifoOpenFailed
        }

        // spawn audiotee via posix_spawn with own process group
        let pid = try Self.spawnAudiotee(binary: binary, stdoutFD: bootstrapFD)
        self.childPID = pid

        // write pid file
        try String(pid).write(toFile: Self.pidPath, atomically: true, encoding: .utf8)

        // open fifo for reading (won't block — child is already writing)
        let rfd = open(fifo, O_RDONLY)
        guard rfd >= 0 else {
            kill(pid, SIGTERM)
            close(bootstrapFD)
            throw AudioRecorderError.fifoOpenFailed
        }
        self.readFD = rfd

        // close bootstrap fd
        close(bootstrapFD)

        // start read loop
        running = true
        elapsedSeconds = 0
        let thread = Thread { [weak self] in self?.readLoop() }
        thread.name = "audiotee-reader"
        thread.qualityOfService = .userInitiated
        thread.start()
        self.readThread = thread
    }

    /// stop recording and return any remaining buffered audio as a WAV file
    func stop() -> (url: URL, offset: TimeInterval)? {
        running = false

        if readFD >= 0 {
            close(readFD)
            readFD = -1
        }

        // wait for read thread to finish and store flush data (brief block, <50ms typical)
        _ = threadDone.wait(timeout: .now() + 1.0)

        // write flush chunk if the read thread left data
        // no silence filter here — short recordings would be lost entirely
        var result: (url: URL, offset: TimeInterval)? = nil
        if let data = flushData, data.count >= Self.bytesPerSample {
            if let url = writeWAV(data) {
                result = (url, flushOffset)
            }
        }
        flushData = nil

        if childPID > 0 {
            let pid = childPID
            childPID = -1
            kill(pid, SIGTERM)
            // wait for exit before removing pid file so cleanupOrphans can find it if we crash
            var status: Int32 = 0
            let waited = waitpid(pid, &status, WNOHANG)
            if waited == 0 {
                // still alive, give it a moment then force kill
                usleep(200_000)
                if waitpid(pid, &status, WNOHANG) == 0 {
                    kill(pid, SIGKILL)
                    waitpid(pid, &status, 0)
                }
            }
        }

        Self.removeStateFiles()
        return result
    }

    static func cleanupOrphans() {
        // try pid file first
        if let pidStr = try? String(contentsOfFile: pidPath, encoding: .utf8),
           let pid = Int32(pidStr.trimmingCharacters(in: .whitespacesAndNewlines)) {
            kill(pid, SIGTERM)
            usleep(100_000)
            kill(pid, SIGKILL)
        }

        // fallback: kill any audiotee processes we missed (e.g. pid file was removed but process survived)
        let task = Process()
        task.executableURL = URL(fileURLWithPath: "/usr/bin/pkill")
        task.arguments = ["-f", "audiotee.*--sample-rate"]
        task.standardOutput = FileHandle.nullDevice
        task.standardError = FileHandle.nullDevice
        try? task.run()
        task.waitUntilExit()

        removeStateFiles()
    }

    // MARK: - posix_spawn

    // spawn audiotee with its own process group (setpgrp equivalent)
    // so TCC evaluates permissions against audiotee's own binary, not us
    private static func spawnAudiotee(binary: String, stdoutFD: Int32) throws -> pid_t {
        // spawn attributes: POSIX_SPAWN_SETPGROUP with pgroup 0 = own PID
        var attr: posix_spawnattr_t? = nil
        var rc = posix_spawnattr_init(&attr)
        guard rc == 0 else { throw POSIXError(POSIXErrorCode(rawValue: rc)!) }
        defer { posix_spawnattr_destroy(&attr) }

        rc = posix_spawnattr_setflags(&attr, Int16(POSIX_SPAWN_SETPGROUP))
        guard rc == 0 else { throw POSIXError(POSIXErrorCode(rawValue: rc)!) }

        rc = posix_spawnattr_setpgroup(&attr, 0)
        guard rc == 0 else { throw POSIXError(POSIXErrorCode(rawValue: rc)!) }

        // file actions: dup2 stdout to FIFO fd, redirect stderr to /dev/null
        var actions: posix_spawn_file_actions_t? = nil
        rc = posix_spawn_file_actions_init(&actions)
        guard rc == 0 else { throw POSIXError(POSIXErrorCode(rawValue: rc)!) }
        defer { posix_spawn_file_actions_destroy(&actions) }

        rc = posix_spawn_file_actions_adddup2(&actions, stdoutFD, STDOUT_FILENO)
        guard rc == 0 else { throw POSIXError(POSIXErrorCode(rawValue: rc)!) }

        // capture stderr for diagnostics (audiotee outputs JSON logs there)
        var stderrPipe: [Int32] = [0, 0]
        guard pipe(&stderrPipe) == 0 else { throw AudioRecorderError.fifoCreationFailed }
        rc = posix_spawn_file_actions_adddup2(&actions, stderrPipe[1], STDERR_FILENO)
        guard rc == 0 else { close(stderrPipe[0]); close(stderrPipe[1]); throw POSIXError(POSIXErrorCode(rawValue: rc)!) }
        rc = posix_spawn_file_actions_addclose(&actions, stderrPipe[0])
        guard rc == 0 else { close(stderrPipe[0]); close(stderrPipe[1]); throw POSIXError(POSIXErrorCode(rawValue: rc)!) }

        let args = [binary, "--sample-rate", "16000"]
        let argv: [UnsafeMutablePointer<CChar>?] = args.map { strdup($0) } + [nil]
        defer { for case let arg? in argv { free(arg) } }

        var pid: pid_t = 0
        rc = posix_spawn(&pid, binary, &actions, &attr, argv, environ)
        guard rc == 0 else { close(stderrPipe[0]); close(stderrPipe[1]); throw POSIXError(POSIXErrorCode(rawValue: rc)!) }

        close(stderrPipe[1]) // close write end in parent
        // log audiotee stderr on background thread
        let stderrFD = stderrPipe[0]
        DispatchQueue.global(qos: .utility).async {
            let fh = FileHandle(fileDescriptor: stderrFD, closeOnDealloc: true)
            while let line = String(data: fh.availableData, encoding: .utf8), !line.isEmpty {
                for part in line.split(separator: "\n") {
                    print("[audiotee] \(part)")
                }
            }
        }

        print("[AudioRecorder] spawned audiotee pid=\(pid) with own process group")
        return pid
    }

    // MARK: - private

    private static func removeStateFiles() {
        try? FileManager.default.removeItem(atPath: fifoPath)
        try? FileManager.default.removeItem(atPath: pidPath)
    }

    private func readLoop() {
        let blockSize = Self.blockBytes
        let samplesPerChunk = Self.sampleRate * chunkSeconds
        let bytesPerChunk = samplesPerChunk * Self.bytesPerSample

        var chunkBuffer = Data(capacity: bytesPerChunk)
        var leftover = Data()  // byte alignment carry
        var silentBlocks = 0
        var totalBytesRead = 0
        var eofCount = 0

        let readBuf = UnsafeMutablePointer<UInt8>.allocate(capacity: blockSize)
        defer { readBuf.deallocate() }

        while running {
            let n = read(readFD, readBuf, blockSize)
            guard n > 0 else {
                if n == 0 {
                    eofCount += 1
                    if eofCount == 1 {
                        print("[AudioRecorder] EOF on FIFO — audiotee may have exited")
                    }
                    usleep(10_000)
                    continue
                }
                break  // read error (fd closed by stop())
            }

            eofCount = 0
            totalBytesRead += n

            var data = leftover + Data(bytes: readBuf, count: n)
            leftover = Data()

            // byte alignment: carry odd byte
            if data.count % Self.bytesPerSample != 0 {
                leftover = data.suffix(1)
                data = data.dropLast(1)
            }

            // silence detection
            let isSilent = Self.isSilent(data)
            if isSilent { silentBlocks += 1 } else { silentBlocks = 0 }

            if silentBlocks > 0 && silentBlocks % Int(30.0 / Self.blockDuration) == 0 {
                print("[AudioRecorder] \(Int(Double(silentBlocks) * Self.blockDuration))s of continuous silence")
            }

            chunkBuffer.append(data)

            if chunkBuffer.count >= bytesPerChunk {
                let duration = Double(chunkBuffer.count / Self.bytesPerSample) / Double(Self.sampleRate)

                if !Self.isChunkSilent(chunkBuffer) {
                    if let url = writeWAV(chunkBuffer) {
                        onChunk(url, elapsedSeconds)
                    }
                } else {
                    print("[AudioRecorder] dropping silent chunk at offset \(String(format: "%.0f", elapsedSeconds))s")
                }

                elapsedSeconds += duration
                chunkBuffer = Data(capacity: bytesPerChunk)
            }
        }

        // store remaining data for stop() to handle synchronously
        if chunkBuffer.count >= Self.bytesPerSample {
            flushData = chunkBuffer
            flushOffset = elapsedSeconds
        }
        print("[AudioRecorder] readLoop done — \(totalBytesRead) bytes read, \(chunkBuffer.count) bytes flushed")
        threadDone.signal()
    }

    private static func isSilent(_ data: Data) -> Bool {
        data.withUnsafeBytes { raw in
            let samples = raw.bindMemory(to: Int16.self)
            var sum: Float = 0
            for s in samples { sum += abs(Float(s) / 32768.0) }
            let mean = sum / Float(max(samples.count, 1))
            return mean < silenceThreshold
        }
    }

    private static func isChunkSilent(_ data: Data) -> Bool {
        isSilent(data)
    }

    private func writeWAV(_ pcmData: Data) -> URL? {
        let dir = outputDir.appendingPathComponent("loopback")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)

        let filename = String(format: "chunk_%.1f.wav", elapsedSeconds)
        let url = dir.appendingPathComponent(filename)

        // wav header for 16-bit mono PCM
        var header = Data()
        let dataSize = UInt32(pcmData.count)
        let fileSize = dataSize + 36

        header.append(contentsOf: "RIFF".utf8)
        header.append(withUnsafeBytes(of: fileSize.littleEndian) { Data($0) })
        header.append(contentsOf: "WAVE".utf8)
        header.append(contentsOf: "fmt ".utf8)
        header.append(withUnsafeBytes(of: UInt32(16).littleEndian) { Data($0) })  // subchunk size
        header.append(withUnsafeBytes(of: UInt16(1).littleEndian) { Data($0) })   // PCM
        header.append(withUnsafeBytes(of: UInt16(1).littleEndian) { Data($0) })   // mono
        header.append(withUnsafeBytes(of: UInt32(Self.sampleRate).littleEndian) { Data($0) })
        header.append(withUnsafeBytes(of: UInt32(Self.sampleRate * Self.bytesPerSample).littleEndian) { Data($0) })
        header.append(withUnsafeBytes(of: UInt16(Self.bytesPerSample).littleEndian) { Data($0) })
        header.append(withUnsafeBytes(of: UInt16(16).littleEndian) { Data($0) })  // bits per sample
        header.append(contentsOf: "data".utf8)
        header.append(withUnsafeBytes(of: dataSize.littleEndian) { Data($0) })

        var wavData = header
        wavData.append(pcmData)

        do {
            try wavData.write(to: url)
            return url
        } catch {
            print("[AudioRecorder] failed to write wav: \(error)")
            return nil
        }
    }
}

enum AudioRecorderError: LocalizedError {
    case audioteeNotFound
    case fifoCreationFailed
    case fifoOpenFailed
    case spawnFailed(Int32)

    var errorDescription: String? {
        switch self {
        case .audioteeNotFound:
            return "audiotee not found. Install from https://github.com/makeusabrew/audiotee"
        case .fifoCreationFailed:
            return "Failed to create FIFO pipe"
        case .fifoOpenFailed:
            return "Failed to open FIFO pipe"
        case .spawnFailed(let code):
            return "posix_spawn failed with code \(code)"
        }
    }
}
