cask "meetingscribe" do
  version "2.4.1"
  sha256 "b8fc0e2a57b830642e98c1fb35cb2831d7c87fffd4c795c073b54f92d825c0c8"

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
