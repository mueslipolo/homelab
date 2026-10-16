# shellcheck shell=bash
# Quality presets shared by reencode.sh and reencode-compare.sh. Sourced.
#
#   name     for                                        qp   skip files below
#   sitcom   sitcoms, old TV, animation, talk shows      27   3.5 Mbit/s
#   series   modern drama series                         24   6 Mbit/s
#   film     films, cinematic, dark or grainy series     22   9 Mbit/s
#
# qp is the GPU encoder's quantizer: lower = better and bigger. The skip
# threshold is about 1.5x what the preset produces, so a file already near
# that size is left alone. Targets (HEVC video): sitcom ~1.5-3 Mbit/s,
# series ~3-5, film ~5-8 at 1080p; see stacks/mediarr/README.md.

# shellcheck disable=SC2034 # used by the scripts that source this file
PRESETS="sitcom series film"

preset_qp() { # <name>
	case $1 in sitcom) echo 27 ;; series) echo 24 ;; film) echo 22 ;; *) return 1 ;; esac
}

preset_min() { # <name>
	case $1 in sitcom) echo 3.5 ;; series) echo 6 ;; film) echo 9 ;; *) return 1 ;; esac
}

is_preset() { preset_qp "$1" >/dev/null; }
