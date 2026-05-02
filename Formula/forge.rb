# frozen_string_literal: true

# Optional Homebrew install (HEAD). After adding this tap:
#   brew tap henildiyora/forge https://github.com/Henildiyora/forge.git
#   brew install --HEAD forge
#
# `pipx install git+https://github.com/Henildiyora/forge.git` remains the
# recommended path until stable PyPI releases ship with checksums.

class Forge < Formula
  desc "Terminal-first AI DevOps CLI (forge index, forge build, forge ask)"
  homepage "https://github.com/Henildiyora/forge"
  license "MIT"
  head "https://github.com/Henildiyora/forge.git", branch: "main"

  depends_on "python@3.12"

  def install
    venv = libexec
    system "python3.12", "-m", "venv", venv
    system venv/"bin/pip", "install", "--upgrade", "pip"
    system venv/"bin/pip", "install", buildpath
    bin.install_symlink venv/"bin/forge"
  end

  test do
    assert_match "Usage", shell_output("#{bin}/forge --help")
  end
end
