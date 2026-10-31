"""MSVC launcher with ASCII relative arguments inside a Unicode project root."""
import os,subprocess
from pathlib import Path


def run_msvc(directory,sources,binary,includes=(),crt=False,object_name=None):
    directory=Path(directory).resolve()
    found=sorted(Path('C:/Program Files/Microsoft Visual Studio').glob('*/Community/VC/Auxiliary/Build/vcvars64.bat'))
    if not found:raise RuntimeError('MSVC x64 Community build environment is unavailable')
    def relative(path):
        # All shipped path components within runtime are ASCII. A Chinese parent
        # is carried by CreateProcessW's working directory, never batch text.
        value=os.path.relpath(Path(path).resolve(),directory)
        value.encode('ascii')
        if any(x in value for x in ('%','!','\r','\n','"')):raise ValueError('Unsafe compiler path')
        return '"'+value+'"'
    args=['cl /nologo /O2 /LD /TC']
    if crt:args.append('/D_CRT_SECURE_NO_WARNINGS')
    args.extend('/I'+relative(p) for p in includes)
    args.extend(relative(p) for p in sources)
    if object_name:args.append('/Fo:'+relative(directory/object_name))
    args.extend(('/link','/OUT:'+relative(binary)))
    text='@echo off\ncall "'+str(found[-1])+'" >nul\nif errorlevel 1 exit /b 1\n'+' '.join(args)+'\n'
    (directory/'build-portable.bat').write_text(text,encoding='ascii')
    # Escaped non-ASCII diagnostic bytes remain writable even when Python and
    # the vendor shell use different Windows encodings. Compiler status is kept.
    return subprocess.run(['cmd','/d','/c','build-portable.bat'],cwd=directory,capture_output=True,text=True,encoding='ascii',errors='backslashreplace')
