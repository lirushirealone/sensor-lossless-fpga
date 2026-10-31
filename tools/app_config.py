"""Source-release adapter; qualified codec/RTL remain unchanged."""
import hashlib,json,os,posixpath,shlex
from pathlib import Path


def configure(root):
    root=Path(root).resolve();runtime=root/'runtime'
    from desktop_gui_v3 import main,history_store,backend
    from desktop_gui_v1 import backend as original_backend
    from desktop_gui_v2 import backend as power_backend
    history=root/'results/user_history';work=root/'results/work';examples=root/'data/examples'
    history.mkdir(parents=True,exist_ok=True)
    main.HISTORY_ROOT=history;main.DEMO=examples
    history_store.HISTORY_ROOT=history;history_store.STAGING_ROOT=work;history_store.ROOT=history
    history_store.publish.__defaults__=(history,False);history_store.decode_to_result.__defaults__=(history,)
    backend.STAGING_ROOT=work
    definition=json.loads((root/'board/runtime_manifest.json').read_bytes())
    # Match the qualified backend's exact bytes across Windows/Linux newlines.
    manifest=(json.dumps(definition,indent=2)+'\n').encode('utf-8')
    bit_sha=definition['file_hashes']['unified-overlay/compressor.bit']

    def provenance(path):
        path=Path(path).resolve();info=dict(name=path.stem,path=str(path),kind='用户导入')
        if path.parent==examples:
            catalog=json.loads((examples/'catalog.json').read_text(encoding='utf-8'))
            row=next((r for r in catalog['files'] if r['file']==path.name),None)
            if row and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']:info.update(row,record=path.stem)
        return info
    main.provenance=provenance

    class SourceBackend(backend.Backend):
        def prepare(self,client):
            self.notify('phase',text='校验源码包并准备独立板端目录')
            sftp=client.open_sftp();home=sftp.normalize('.')
            release=posixpath.join(home,'projects/sensor_lossless_fpga',hashlib.sha256(manifest).hexdigest()[:16])
            def mkdir(path):
                current='/'
                for part in path.strip('/').split('/'):
                    current=posixpath.join(current,part)
                    try:sftp.stat(current)
                    except FileNotFoundError:sftp.mkdir(current)
            try:
                # Share the existing workbench's board ownership lock, including
                # on a fresh standard xilinx-user PYNQ image.
                mkdir('/home/xilinx/projects/fpga_compression')
                mkdir(release+'/jobs')
                for name,expected in definition['file_hashes'].items():
                    self.check();local=runtime/name;body=local.read_bytes()
                    if hashlib.sha256(body).hexdigest()!=expected:raise ValueError('源码包文件校验失败：'+name)
                    target=release+'/'+name;mkdir(posixpath.dirname(target))
                    try:
                        with sftp.open(target,'rb') as f:previous=f.read()
                        if previous!=body:raise ValueError('已有板端独立副本发生变化：'+name)
                    except FileNotFoundError:
                        with sftp.open(target,'wb') as f:f.write(body)
                target=release+'/gui-runtime-manifest.json'
                try:
                    with sftp.open(target,'rb') as f:previous=f.read()
                    if previous!=manifest:raise ValueError('板端清单不一致。')
                except FileNotFoundError:
                    with sftp.open(target,'wb') as f:f.write(manifest)
            finally:sftp.close()
            self.remote_release=release
            return release,definition

        def connect(self):
            client=self.client()
            try:
                release,definition=self.prepare(client)
                return dict(status='CONNECTED',version='unified-v1',runtime=release,bit_sha256=bit_sha,hardware=backend.CONFIG['hardware'])
            finally:client.close()

    main.Backend=SourceBackend
    class SourceWindow(main.Window):
        def __init__(self):
            super().__init__()
            self.host.setText(os.environ.get('PYNQ_HOST',''))
            self.host.setPlaceholderText('板卡 IP 地址')
            self.password.setText(os.environ.get('PYNQ_PASSWORD',''))
            self.user.setText(os.environ.get('PYNQ_USER','xilinx'))
    return SourceWindow,SourceBackend
