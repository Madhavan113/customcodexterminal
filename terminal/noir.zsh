# Colorful path, branch, marker, and exit status. Source this from an interactive zsh.
[[ -o interactive ]] || return 0

if [[ -z ${NO_COLOR-} ]]; then
  export CLICOLOR=1
  export LSCOLORS='GxFxCxDxBxegedabagacad'
  export LS_COLORS='di=36:ln=35:ex=32:so=35:pi=33:bd=33:cd=33'
  zmodload -i zsh/complist 2>/dev/null
  zstyle ':completion:*' menu select
  zstyle ':completion:*' list-colors ${(s.:.)LS_COLORS}
fi

_codex_noir_prompt() {
  local last_status=$?
  emulate -L zsh
  local branch='' muted='%F{8}' accent='%F{13}' normal='%f'
  local path_color='%F{14}' marker_color='%F{11}' alert='%F{9}'
  local marker='››'
  [[ -n ${NO_COLOR-} ]] && muted='' accent='' normal='' path_color='' marker_color='' alert=''

  if (( $+commands[git] )); then
    branch=$(command git symbolic-ref --quiet --short HEAD 2>/dev/null) ||
      branch=$(command git rev-parse --short HEAD 2>/dev/null) || branch=''
    # Branch text can never become prompt escapes or shell substitutions.
    branch=${branch//[^A-Za-z0-9._\/-]/?}
    (( ${#branch} > 40 )) && branch="${branch[1,40]}…"
  fi

  # Colorful but calm: cyan path, magenta branch, yellow marker, red exit status.
  typeset -g PROMPT="${muted}─${normal} ${path_color}%~${normal}"
  [[ -n $branch ]] && PROMPT+=" ${muted}/${normal} ${accent}${branch}${normal}"
  if (( last_status )); then
    PROMPT+=" ${alert}· exit ${last_status}${normal}"
  fi
  PROMPT+=$'\n'"${marker_color}${marker}${normal} "
}

autoload -Uz add-zsh-hook
add-zsh-hook -d precmd _codex_overdrive_prompt 2>/dev/null
add-zsh-hook -d precmd _codex_noir_prompt 2>/dev/null
add-zsh-hook precmd _codex_noir_prompt
_codex_noir_prompt
