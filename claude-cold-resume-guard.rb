class ClaudeColdResumeGuard < Formula
  desc "Prevent cold-cache token spikes and quota exhaustion in Claude Code"
  homepage "https://l2cache.app/claude-pro-weekly-limit"
  url "https://github.com/amvotech/claude-cold-resume-guard/archive/refs/tags/v0.1.0.tar.gz"
  license "MIT"

  depends_on "python@3.11"

  def install
    bin.install "guard.py" => "cold-resume-guard"
    bin.install_symlink bin/"cold-resume-guard" => "claude-guard"
  end

  test do
    system "#{bin}/cold-resume-guard"
  end
end
