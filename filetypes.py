# filetypes.py - allowed MIME types and extension categories for ShareBin

ALLOWED_MIME_TYPES = {
    "image/jpeg", "image/png", "image/gif", "image/webp",
    "image/bmp", "image/svg+xml", "image/vnd.adobe.photoshop",
    "video/mp4", "video/webm", "video/ogg",
    "video/quicktime", "video/x-msvideo",
    "audio/mpeg", "audio/ogg", "audio/wav", "audio/webm", "audio/mp4",
    "application/pdf", "text/plain", "text/markdown", "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/zip", "application/x-rar-compressed",
    "application/x-7z-compressed",
    "text/html", "text/css", "text/javascript", "application/json",
    "text/x-python", "text/x-java", "text/x-c", "text/x-cpp",
    # design / creative
    "application/x-photoshop", "image/x-psd", "application/illustrator",
    "application/postscript", "image/x-indesign", "image/x-raw",
    "image/tiff", "image/x-icns", "image/x-icon",
    "model/gltf-binary", "model/gltf+json",
}

FILE_CATEGORIES = {
    "image": [
        "jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "tif",
        "ico", "icns", "heic", "heif", "avif", "jfif",
        "svg", "eps", "ai", "cdr", "wmf", "emf",
        "psd", "xcf", "kra",
    ],
    "video": [
        "mp4", "webm", "ogg", "mov", "avi", "mkv", "flv", "wmv",
        "m4v", "mpg", "mpeg", "m2ts", "mts", "ts", "3gp", "3g2",
        "ogv", "vob", "divx", "asf", "amv",
    ],
    "audio": [
        "mp3", "ogg", "wav", "m4a", "flac", "aac", "wma", "alac",
        "opus", "aiff", "aif", "au", "mid", "midi",
        "amr", "mka", "wv", "tta", "tak",
    ],
    "pdf": ["pdf"],
    "text": [
        "txt", "log", "md", "rst", "nfo",
        "py", "js", "mjs", "cjs", "ts", "tsx", "jsx",
        "html", "htm", "xhtml", "css", "scss", "sass", "less",
        "json", "jsonl", "xml", "yaml", "yml", "toml",
        "java", "kt", "kts", "groovy",
        "c", "cpp", "cc", "cxx", "h", "hpp", "hxx", "cs",
        "go", "rs", "rb", "php", "phtml", "pl", "pm",
        "sh", "bash", "zsh", "fish", "ps1", "bat", "cmd",
        "sql",
        "r", "rmd", "jl", "dart", "swift",
        "conf", "cfg", "ini", "properties", "env", "rc",
        "tex", "bib", "sty", "cls", "ltx",
        "lua", "vhd", "vhdl", "scala", "clj", "cljs",
        "elixir", "ex", "exs", "erl", "hrl", "cr",
    ],
    "document": [
        "doc", "docx", "dot", "dotx", "docm",
        "xls", "xlsx", "xlsm", "xlsb", "xlt", "xltx", "xlam",
        "ppt", "pptx", "pptm", "pps", "ppsx", "pot", "potx",
        "odt", "ods", "odp", "odg", "odf", "odc", "odi",
        "ott", "ots", "otp", "otg",
        "rtf", "wpd", "wps", "pages", "numbers", "key",
        "mpp", "mpx", "vsd", "vsdx", "one", "onepkg",
    ],
    "archive": [
        "zip", "rar", "7z", "tar", "gz", "bz2",
        "xz", "lz", "lzma", "lz4", "zst", "br",
        "deb", "pkg", "dmg", "iso", "img",
        "cab", "arj", "lzh", "lha", "ace", "zoo", "arc", "pak",
        "tgz", "tbz2", "txz", "tlz", "taz",
    ],
    "design": [
        "psd", "ai", "eps", "indd", "indl", "idml",
        "xcf", "kra", "ora",
        "afdesign", "afphoto", "afpub", "sketch", "xd",
        "fig", "graffle", "visio", "vsdx", "dxf", "dwg",
        "svg", "cdr", "cmx", "wmf", "emf",
    ],
    "3d": [
        "glb", "gltf", "obj", "fbx", "stl", "3ds", "dae", "ply",
        "blend", "blend1", "max", "ma", "mb", "c4d", "lwo", "lws",
        "usd", "usda", "usdc", "usdz", "abc", "x3d", "vrml", "wrl",
        "step", "stp", "iges", "igs",
    ],
    "font": [
        "ttf", "otf", "woff", "woff2", "eot", "fnt", "fon",
        "pfb", "pfm", "afm", "sfd", "ufo", "ttc", "dfont",
    ],
    "database": [
        "sqlite", "db", "sqlite3", "db3", "dbf", "mdb", "accdb",
        "fdb", "gdb", "edb", "dat", "csv", "tsv",
    ],
    "ebook": [
        "epub", "mobi", "azw", "azw3", "kfx", "prc", "pdb",
        "fb2", "djvu", "djv", "cbr", "cbz", "cb7", "cbt",
    ],
    "cad": [
        "dwg", "dxf", "dgn", "dwf", "dwfx",
        "3dm", "3ds", "blend", "obj", "stl", "skp",
        "iam", "ipt", "idw", "catpart", "catproduct", "cgr",
        "prt", "asm", "drw", "sldprt", "sldasm", "slddrw",
    ],
    "executable": [
        "msi", "app", "appimage", "snap", "deb", "rpm",
        "jar", "war", "ear", "apk", "ipa",
    ],
}