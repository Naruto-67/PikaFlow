// GitHub repo details — sourced from env so the panel works for any repo
export const REPO_OWNER = import.meta.env.VITE_REPO_OWNER || 'Naruto-67'
export const REPO_NAME  = import.meta.env.VITE_REPO_NAME  || 'PikaFlow'
export const GH_API     = 'https://api.github.com'
export const REPO_PATH  = `${REPO_OWNER}/${REPO_NAME}`

