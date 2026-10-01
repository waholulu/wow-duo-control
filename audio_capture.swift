import Foundation
import AVFoundation
import CoreMedia
import CoreAudio

// Explicit input selection only. No default-device or microphone fallback.
let outputLock = NSLock()
func emit(_ row: [String: Any]) {
    outputLock.lock()
    defer { outputLock.unlock() }
    if let data = try? JSONSerialization.data(withJSONObject: row) {
        print(String(data: data, encoding: .utf8)!)
        fflush(stdout)
    }
}

let args = CommandLine.arguments
func option(_ key: String) -> String? {
    guard let index = args.firstIndex(of: key), index + 1 < args.count else { return nil }
    return args[index + 1]
}
let devices = AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone, .external],
    mediaType: .audio, position: .unspecified).devices
if args.contains("--list") {
    emit(["devices": devices.map { ["name": $0.localizedName, "uid": $0.uniqueID] },
          "authorization_status": AVCaptureDevice.authorizationStatus(for: .audio).rawValue])
    exit(0)
}
guard let uid = option("--device") else {
    emit(["error": "Explicit --device UID required; enumerate with --list"])
    exit(2)
}
guard let device = devices.first(where: { $0.uniqueID == uid }) else {
    emit(["error": "Configured audio input unavailable"])
    exit(2)
}
let permission = DispatchSemaphore(value: 0)
var granted = false
AVCaptureDevice.requestAccess(for: .audio) { yes in granted = yes; permission.signal() }
if permission.wait(timeout: .now() + 30) == .timedOut || !granted {
    emit(["error": "Audio capture permission unavailable"])
    exit(3)
}

final class Samples: NSObject, AVCaptureAudioDataOutputSampleBufferDelegate {
    weak var session: AVCaptureSession?
    var sequence = 0
    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer, from connection: AVCaptureConnection) {
        let hostClock = CMClockGetHostTimeClock()
        let now = CMTimeGetSeconds(CMClockGetTime(hostClock))
        guard let sourceClock = session?.synchronizationClock else {
            emit(["error": "Audio synchronization clock unavailable"]); exit(7)
        }
        // Preserve actual acquisition time. Anchoring the first PTS to callback
        // arrival would erase capture latency from response measurements.
        let pts = CMTimeGetSeconds(CMSyncConvertTime(
            CMSampleBufferGetPresentationTimeStamp(sample), from: sourceClock, to: hostClock))
        guard pts.isFinite, let format = CMSampleBufferGetFormatDescription(sample),
              let desc = CMAudioFormatDescriptionGetStreamBasicDescription(format),
              let block = CMSampleBufferGetDataBuffer(sample) else {
            emit(["error": "Audio sample format or timestamp unavailable"]); exit(7)
        }
        var length = 0
        var pointer: UnsafeMutablePointer<Int8>?
        guard CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: nil,
                    totalLengthOut: &length, dataPointerOut: &pointer) == kCMBlockBufferNoErr,
              let bytes = pointer else {
            emit(["error": "Audio PCM buffer unavailable"]); exit(7)
        }
        sequence += 1
        let data = Data(bytes: bytes, count: length)
        emit(["kind": "samples", "sequence": sequence, "host_time": now,
              "sample_time": pts, "rate": desc.pointee.mSampleRate,
              "channels": desc.pointee.mChannelsPerFrame, "bits": desc.pointee.mBitsPerChannel,
              "float": (desc.pointee.mFormatFlags & kAudioFormatFlagIsFloat) != 0,
              "little_endian": (desc.pointee.mFormatFlags & kAudioFormatFlagIsBigEndian) == 0,
              "pcm": data.base64EncodedString()])
    }
}

do {
    let session = AVCaptureSession()
    let input = try AVCaptureDeviceInput(device: device)
    let output = AVCaptureAudioDataOutput()
    output.audioSettings = [AVFormatIDKey: kAudioFormatLinearPCM,
        AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1,
        AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false,
        AVLinearPCMIsBigEndianKey: false]
    guard session.canAddInput(input), session.canAddOutput(output) else {
        emit(["error": "Audio session cannot use configured input"]); exit(4)
    }
    session.addInput(input)
    session.addOutput(output)
    let delegate = Samples()
    delegate.session = session
    output.setSampleBufferDelegate(delegate, queue: DispatchQueue(label: "game.audio"))
    NotificationCenter.default.addObserver(forName: AVCaptureDevice.wasDisconnectedNotification,
        object: device, queue: nil) { _ in
            emit(["error": "Configured audio device disconnected"]); exit(5)
        }
    NotificationCenter.default.addObserver(forName: AVCaptureSession.runtimeErrorNotification,
        object: session, queue: nil) { notification in
            let reason = notification.userInfo?[AVCaptureSessionErrorKey] ?? "unknown runtime error"
            emit(["error": "Audio capture failed: \(reason)"]); exit(8)
        }
    emit(["kind": "ready", "device_uid": uid, "clock": "mach_absolute_time",
          "host_time": CMTimeGetSeconds(CMClockGetTime(CMClockGetHostTimeClock()))])
    session.startRunning()
    RunLoop.current.run()
} catch {
    emit(["error": String(describing: error)]); exit(6)
}
