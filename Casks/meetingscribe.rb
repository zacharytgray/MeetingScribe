cask "meetingscribe" do
  version "2.0.1"
  sha256 "141038d937afb8ccd21c6f99bd225969b6aeeaf9fecc5a428ecb9d8e0df267a1"

  url "https://github.com/zacharytgray/MeetingScribe/releases/download/v#{version}/MeetingScribe-#{version}.dmg"
  name "MeetingScribe"
  desc "Meeting recorder, transcriber, and Claude Code processor for macOS"
  homepage "https://github.com/zacharytgray/MeetingScribe"

  depends_on macos: ">= :sonoma"

  app "MeetingScribe.app"

  postflight do
    ohai "MeetingScribe requires audiotee for system audio capture."
    ohai "Install: git clone https://github.com/makeusabrew/audiotee && cd audiotee && swift build -c release && cp .build/release/audiotee ~/.local/bin/"
    ohai "Then grant Screen & System Audio Recording permission to audiotee in System Settings."
  end

  zap trash: [
    "~/Library/Application Support/MeetingScribe",
    "~/.meetingscribe",
  ]
end
