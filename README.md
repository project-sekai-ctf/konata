# kona

[![ci](https://github.com/project-sekai-ctf/konata/actions/workflows/lint_test.yml/badge.svg)](https://github.com/project-sekai-ctf/konata/actions/workflows/lint_test.yml)
[![pypi](https://img.shields.io/pypi/v/konata.svg)](https://pypi.python.org/project/konata)
[![license](https://img.shields.io/github/license/project-sekai-ctf/konata.svg)](https://github.com/project-sekai-ctf/konata/blob/master/LICENSE)

kona is a CTF tool for managing challenges and deploying them across multiple CTF platforms. It aims to fix the problems we have experienced while hosting CTFs.

**kona is a work in progress. while it's cool and nice, please refrain from actually using it for now.**

## 1.0.0 Roadmap:

- [x] global config
- [x] TOML schema loading support
- [x] YAML schema loading support
- [x] rCTF support
- [x] CTFd support
- [x] Challenge syncing
- [x] docker images building/pushing
- [x] k8s manifests deployment
- [x] klodd support
- [x] Delay for RBACs, CRDs when applying k8s manifests
- [x] diff binaries in attachments and in challenge dir
- [ ] better diff displaying
- [ ] discord webhook for logs
- [ ] Option to not compress attachments and attach as-is
- [ ] kCTF support
- [ ] delete challenges that are missing in repo (should be opt-in)
- [ ] cover with tests
- [ ] test docker/k8s gcloud auth stuff, should be fineTM though
- [ ] github ci action - run only changed stuff
- [ ] documentation

## Acknowledgements

* [rcds](https://github.com/redpwn/rcds) - inspiration
* [idekctf](https://github.com/idekctf) (JoshL & Trixter) - rCTF api reference, inspiration
* [ctfcli](https://github.com/ctfd/ctfcli) - CTFd api reference, inspiration
