import io

p = ".github/workflows/build-windows.yml"
s = io.open(p, encoding="utf-8").read()

old = """      - name: Upload artifact
        uses: actions/upload-artifact@v4
        with:
          name: bacpro-windows
          path: dist/bacpro.exe
          if-no-files-found: error

      - name: Attach to release
        if: startsWith(github.ref, 'refs/tags/')
        uses: softprops/action-gh-release@v2
        with:
          files: dist/bacpro.exe
"""
new = """      - name: Build installer (optional)
        continue-on-error: true
        shell: pwsh
        run: |
          choco install innosetup --no-progress -y
          $iscc = "${env:ProgramFiles(x86)}\\Inno Setup 6\\ISCC.exe"
          if (-not (Test-Path $iscc)) {
            $iscc = "${env:ProgramFiles}\\Inno Setup 6\\ISCC.exe"
          }
          if (Test-Path $iscc) {
            & $iscc "installer\\bacpro.iss"
          } else {
            Write-Warning "Inno Setup not found - skipping installer."
          }

      - name: Upload portable exe
        uses: actions/upload-artifact@v4
        with:
          name: bacpro-windows
          path: dist/bacpro.exe
          if-no-files-found: error

      - name: Upload installer
        uses: actions/upload-artifact@v4
        with:
          name: bacpro-windows-installer
          path: installer/Output/bacpro-setup.exe
          if-no-files-found: warn

      - name: Attach to release
        if: startsWith(github.ref, 'refs/tags/')
        uses: softprops/action-gh-release@v2
        with:
          files: |
            dist/bacpro.exe
            installer/Output/bacpro-setup.exe
"""
assert old in s
s = s.replace(old, new, 1)
io.open(p, "w", encoding="utf-8").write(s)
print("ok")
