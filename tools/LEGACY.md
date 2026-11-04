# Legacy 2.7 tools

The scripts in this directory that import `AsyncSmartboxSession`,
`smartbox.error`, `smartbox.socket` or `UpdateManager` are from the 2.7
implementation. They remain here for the historical record and for
reproducing the old probes; they are not part of the 3.0 package.

Use the `v2.7.0` tag to run them, or port the workflow to the 3.0
`SmartboxClient` / `SmartboxEngine` API. The `smartbox` CLI covers the main
inventory, read, write and `ws_user` listen paths:

```sh
smartbox --help
```
