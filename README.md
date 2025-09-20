# kona

kona is a CTF tool for managing challenges and deploying them across multiple CTF platforms. It aims to fix the problems we have experienced while hosting CTFs.

**kona is a work in progress. while it's cool and nice, please refrain from actually using it for now.**

## 1.0.0 Roadmap:

- [x] global config
- [x] TOML schema loading support
- [x] YAML schema loading support
- [x] rCTF support
- [x] CTFd support
- [x] Challenge syncing
- [ ] docker images building/pushing
- [ ] k8s manifests deployment
- [ ] kCTF support
- [ ] klodd support
- [ ] diff binaries in attachments and in challenge dir
- [ ] github ci action
- [ ] discord webhook for logs
- [ ] delete challenges that are missing in repo (should be opt-in)
- [ ] cover with tests
- [ ] documentation

## Acknowledgements

* [rcds](https://github.com/redpwn/rcds) - inspiration
* [idekctf](https://github.com/idekctf) (JoshL & Trixter) - rCTF api reference, inspiration
* [ctfcli](https://github.com/ctfd/ctfcli) - CTFd api reference, inspiration
