cask "meetingscribe" do
  version "2.4.2"
  sha256 "89abb372a0b9ca9fe81e34367af40b1e4cc57e2772b5fffa555f2d69eb203b22"

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
