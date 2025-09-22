# kona-test-infra

We are deploying ctf platforms for testing and setting up them the way so that our credentials will always be the same.

Services with forwarded ports:
* rctf - `:3918`
* ctfd - `:3919`
* registry - `:5111`
* k3s - `:6443`

Nothing is persistent and will be reset on every restart.
