# Publishing to the public repository

`tools/publish.sh <checkout of the public repo> [version]` exports a curated copy of this development repository (see the header of
the script for what goes out and what stays here) and commits it in the public checkout as one commit per publication.

`publish-banned-words.txt` lists what must never appear in the public copy; a hit aborts the export. `publish-allowed-lines.txt` holds
the exact lines that are allowed anyway: the constants that keep data written by earlier versions readable (encryption salt, passkey
user handle, file-encryption associated data, previous export and catalogue formats, browser key migration, a historical migration
default). Changing any of them would destroy or orphan existing data, so they stay as they are.
