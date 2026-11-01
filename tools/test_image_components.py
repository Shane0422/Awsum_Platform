"""Exercise the compiled shared Svelte components in real Chromium."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
BROWSER = Path(os.environ.get("AWSUM_REPORT_CHROME", r"C:\Program Files\Google\Chrome\Application\chrome.exe"))


@unittest.skipUnless(BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class SharedImageComponentTests(unittest.TestCase):
    def test_local_preview_validation_cancel_form_payload_and_avatar_fallback(self):
        script = r"""
const assert = (condition, message) => { if (!condition) throw new Error(message); };
const tick = () => new Promise(resolve => setTimeout(resolve, 20));
const wait = async check => {for(let n=0;n<200;n++){if(check())return;await tick();}throw new Error('Image UI timed out');};
const find = id => document.getElementById(id);
let networkCalls = 0;
window.fetch = () => { networkCalls++; throw new Error('Uploader must not send requests'); };
let created = [], revoked = [];
const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL);
URL.createObjectURL = value => { const url = create(value); created.push(url); return url; };
URL.revokeObjectURL = url => { revoked.push(url); revoke(url); };
(async () => {
  const uploader = AwsumImages.createUploader(find('uploader'), {idPrefix:'test', previewSize:120, fit:'cover'});
  const canvas = document.createElement('canvas'); canvas.width=8; canvas.height=8;
  const saved = canvas.toDataURL('image/png');
  const photo = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
  const select = file => {
    const transfer = new DataTransfer(); transfer.items.add(file);
    find('testFile').files=transfer.files; find('testFile').dispatchEvent(new Event('change', {bubbles:true}));
  };
  uploader.reset(saved, true); await tick();
  assert(find('testPreview').src === saved, 'existing preview');
  const unchanged = uploader.formData({name:'renamed'});
  assert(!unchanged.has('image') && !unchanged.has('image_url') && !unchanged.has('image_path'),
    'saved preview URL is never resubmitted as an upload');
  assert(unchanged.get('remove_image') === 'false', 'account-only edit preserves photo');
  find('testRemove').click(); await tick();
  assert(!find('testPreview'), 'deferred removal preview');
  assert(uploader.formData({name:'user'}).get('remove_image') === 'true', 'remove form field');
  uploader.reset(saved, true); await tick();
  assert(find('testPreview').src === saved, 'cancel restores saved preview');
  select(new File(['<svg/>'], 'bad.svg', {type:'image/svg+xml'})); await tick();
  let blocked=false;
  try { uploader.formData({}); } catch (error) { blocked=true; }
  assert(blocked && find('testError').textContent.includes('Choose'), 'invalid type blocks save');
  select(new File([new Uint8Array(5*1024*1024+1)], 'big.png', {type:'image/png'})); await tick();
  assert(find('testError').textContent.includes('5 MB'), '5 MB boundary');
  select(new File([photo], 'photo.png', {type:'image/png'})); await tick();
  await wait(()=>document.querySelector('dialog .apply')&&!document.querySelector('dialog .apply').disabled);
  document.querySelector('dialog .apply').click();
  await wait(()=>{try{return !!uploader.formData({}).get('image');}catch{return false;}});
  const body=uploader.formData({name:'user'});
  assert(body.get('image').name === 'image.webp', 'pending common processed file');
  assert(body.get('remove_image') === 'false', 'replacement clears removal');
  assert(JSON.parse(body.get('payload')).name === 'user', 'JSON form payload');
  assert(getComputedStyle(find('testPreview')).objectFit === 'cover', 'profile cover');
  assert(find('testPreview').parentElement.getBoundingClientRect().width === 120, '120px preview');
  uploader.setDisabled(true);
  assert(find('testFile').disabled && find('testChoose').disabled && find('testRemove').disabled, 'save disables uploader');
  uploader.reset(saved, true); await tick();
  assert(!uploader.formData({}).has('image'), 'cancel discards selected file');
  assert(revoked.includes(created[0]), 'cancel revokes blob URL');
  select(new File(['corrupt'], 'bad.png', {type:'image/png'})); await tick();
  await wait(()=>document.querySelector('dialog .error')?.textContent.includes('cannot be decoded'));
  blocked=false; try { uploader.formData({}); } catch(error) { blocked=true; }
  assert(blocked, 'decode failure blocks save');
  document.querySelector('dialog').dispatchEvent(new Event('cancel',{cancelable:true}));await tick();
  find('testRemove').click(); await tick();
  assert(uploader.formData({}).get('remove_image') === 'true', 'clear invalid selection');
  const avatar=AwsumImages.createAvatar(find('avatar'), {imageUrl:'missing.png',size:36,name:'Test'});
  await tick();
  assert(!!find('avatar').querySelector('.awsum-avatar-default'), 'missing avatar fallback');
  avatar.setImage(saved); await tick();
  assert(find('avatar').querySelector('img').src === saved, 'avatar refresh');
  assert(find('avatar').firstElementChild.getBoundingClientRect().width === 36, '36px list avatar');
  assert(getComputedStyle(find('avatar').querySelector('img')).objectFit === 'cover', 'avatar cover');
  AwsumImages.updateCurrentAvatar(1,saved); await tick();
  assert(document.querySelector('[data-current-user-avatar] img').src === saved, 'header refresh');
  await uploader.destroy(); await avatar.destroy();
  assert(networkCalls === 0, 'no save/delete during local actions');
  assert(created.every(url=>revoked.includes(url)), 'all blob URLs released');
  find('result').textContent=JSON.stringify({ok:true, blobs:created.length, networkCalls});
})().catch(error=>{find('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_component_script(script)

    def test_profile_crop_geometry_cancel_apply_formats_and_resource_cleanup(self):
        script = r"""
const assert = (condition,message) => {if(!condition) throw new Error(message);};
const tick = () => new Promise(resolve=>setTimeout(resolve,30));
const wait = async check => {for(let n=0;n<200;n++){if(check())return;await tick();}throw new Error('Timed out waiting for crop UI');};
const find=id=>document.getElementById(id);
let networkCalls=0,created=[],revoked=[];
window.fetch=()=>{networkCalls++;throw new Error('No network during crop');};
const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL);
URL.createObjectURL=v=>{const url=create(v);created.push(url);return url;};
URL.revokeObjectURL=url=>{revoked.push(url);revoke(url);};
(async()=>{
  const uploader=AwsumImages.createUploader(find('uploader'),{idPrefix:'profile',fit:'contain'});
  const source=document.createElement('canvas');source.width=100;source.height=200;
  const context=source.getContext('2d');context.fillStyle='#3366cc';context.fillRect(0,0,100,200);
  const saved=source.toDataURL();
  uploader.reset(saved,true);await tick();
  const select=async(width,height,type,name)=>{
    source.width=width;source.height=height;
    const gradient=context.createLinearGradient(0,0,width,height);
    gradient.addColorStop(0,'#3366cc');gradient.addColorStop(1,'#ee2211');
    context.fillStyle=gradient;context.fillRect(0,0,width,height);
    const blob=await new Promise(r=>source.toBlob(r,type));
    const transfer=new DataTransfer();transfer.items.add(new File([blob],name,{type}));
    find('profileFile').files=transfer.files;find('profileFile').dispatchEvent(new Event('change',{bubbles:true}));
    await wait(()=>document.querySelector('dialog[open] button.apply:not(:disabled)'));
  };
  await select(100,200,'image/jpeg','Portrait Photo.JPG');
  let blocked=false;try{uploader.formData({});}catch{blocked=true;}
  assert(blocked,'cannot save while crop is undecided');
  document.querySelector('dialog').dispatchEvent(new Event('cancel',{cancelable:true}));await tick();
  assert(!document.querySelector('dialog'),'Escape cancels crop');
  assert(!uploader.formData({}).has('image')&&find('profilePreview').src===saved,'cancel preserves saved image');
  for(const [w,h,type,name] of [[100,200,'image/jpeg','portrait.jpg'],[1200,300,'image/png','wide.png'],
      [300,1200,'image/webp','tall.webp'],[4000,3000,'image/jpeg','phone.jpg']]){
    await select(w,h,type,name);
    const dialog=document.querySelector('dialog'),canvas=dialog.querySelector('canvas'),slider=dialog.querySelector('input');
    assert(dialog.matches(':modal'),'native modal blocks background interaction');
    slider.value='3';slider.dispatchEvent(new Event('input',{bubbles:true}));await tick();
    const before=canvas.toDataURL();
    for(let i=0;i<12;i++)canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
    assert(before!==canvas.toDataURL(),'keyboard pan moves image');
    for(let i=0;i<100;i++)canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));
    canvas.setPointerCapture=()=>{};
    canvas.dispatchEvent(new PointerEvent('pointerdown',{pointerId:1,pointerType:'touch',button:0,clientX:0,clientY:0,bubbles:true}));
    canvas.dispatchEvent(new PointerEvent('pointermove',{pointerId:1,pointerType:'touch',clientX:99999,clientY:-99999,bubbles:true}));
    canvas.dispatchEvent(new PointerEvent('pointerup',{pointerId:1,bubbles:true}));
    for(const [x,y] of [[0,0],[299,0],[0,299],[299,299]])assert(canvas.getContext('2d').getImageData(x,y,1,1).data[3]===255,'crop covers all corners');
    [...dialog.querySelectorAll('button')].find(x=>x.textContent==='Reset').click();await tick();
    assert(slider.value==='1','reset restores centered contain zoom');
    slider.value='2';slider.dispatchEvent(new Event('input',{bubbles:true}));await tick();
    canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true}));
    canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowUp',bubbles:true}));
    const expected=[...canvas.getContext('2d').getImageData(75,75,1,1).data];
    dialog.querySelector('button.apply').click();
    await wait(()=>!document.querySelector('dialog'));
    await wait(()=>{try{return !!uploader.formData({}).get('image');}catch{return false;}});
    const file=uploader.formData({name:'user'}).get('image');
    assert(file.name==='image.webp'&&file.type==='image/webp','processed file not original filename');
    const bitmap=await createImageBitmap(file);
    assert(bitmap.width===512&&bitmap.height===512,'512x512 output');
    const output=document.createElement('canvas');output.width=512;output.height=512;
    output.getContext('2d').drawImage(bitmap,0,0);
    const actual=[...output.getContext('2d').getImageData(128,128,1,1).data];
    assert(actual.every((value,index)=>Math.abs(value-expected[index])<10),'output matches adjusted frame, not original center crop');
    bitmap.close();
    assert(file.size<5*1024*1024,'small processed output');
    const appliedPreview=find('profilePreview').src;
    await select(200,100,'image/png','another.png');
    document.querySelector('dialog').dispatchEvent(new Event('cancel',{cancelable:true}));await tick();
    assert(uploader.formData({}).get('image')===file&&find('profilePreview').src===appliedPreview,'cancel second crop keeps first pending result');
    uploader.reset(saved,true);await tick();
    assert(!uploader.formData({}).has('image'),'Cancel Edit discards applied crop');
  }
  const chooseInvalid=(file)=>{const d=new DataTransfer();d.items.add(file);find('profileFile').files=d.files;
    find('profileFile').dispatchEvent(new Event('change',{bubbles:true}));};
  chooseInvalid(new File([new Uint8Array(5*1024*1024+1)],'big.png',{type:'image/png'}));await tick();
  assert(find('profileError').textContent.includes('5 MB'),'oversize is friendly error');
  chooseInvalid(new File(['bad'],'../photo.png',{type:'image/png'}));await tick();
  assert(find('profileError').textContent.includes('without directories'),'reject traversal before processing');
  chooseInvalid(new File(['bad image'],'bad.png',{type:'image/png'}));
  await wait(()=>document.querySelector('dialog .error')?.textContent.includes('cannot be decoded'));
  document.querySelector('dialog').dispatchEvent(new Event('cancel',{cancelable:true}));await tick();
  assert(!uploader.formData({}).has('image'),'bad decode cannot become a pending upload');
  uploader.reset(saved,true);find('profileRemove').click();await tick();
  assert(uploader.formData({}).get('remove_image')==='true','remove remains deferred');
  uploader.reset(saved,true);await tick();
  assert(uploader.formData({}).get('remove_image')==='false','Cancel Edit restores remove state');
  await uploader.destroy();
  assert(networkCalls===0,'no writes during apply/remove/cancel');
  assert(created.every(url=>revoked.includes(url)),'original and output blobs revoked');
  find('result').textContent=JSON.stringify({ok:true,networkCalls});
})().catch(error=>{find('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_component_script(script)

    def test_common_zoom_out_contain_background_aspect_ratio_and_reset(self):
        script = r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,25));
const wait=async check=>{for(let n=0;n<200;n++){if(check())return;await tick();}throw Error('Timed out');};
(async()=>{
  const uploader=AwsumImages.createUploader(document.getElementById('uploader'),{idPrefix:'zoom'});
  const pixel=(canvas,x,y)=>[...canvas.getContext('2d').getImageData(x,y,1,1).data];
  const white=value=>value[3]===255&&value.slice(0,3).every(v=>v>245);
  const bounds=canvas=>{
    const data=canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
    let left=canvas.width,right=-1,top=canvas.height,bottom=-1;
    for(let y=0;y<canvas.height;y++)for(let x=0;x<canvas.width;x++){
      const i=(y*canvas.width+x)*4;
      if(data[i]<180&&data[i+1]<180&&data[i+2]<180){
        left=Math.min(left,x);right=Math.max(right,x);top=Math.min(top,y);bottom=Math.max(bottom,y);
      }
    }
    return {left,right,top,bottom,width:right-left+1,height:bottom-top+1};
  };
  for(const [w,h,type,transparent] of [
    [300,1200,'image/jpeg',false],[1200,300,'image/png',false],
    [400,400,'image/webp',false],[400,600,'image/jpeg',false],
    [300,600,'image/png',true],[40,2000,'image/webp',false]]){
    const source=document.createElement('canvas');source.width=w;source.height=h;
    const ctx=source.getContext('2d');ctx.fillStyle='#222222';ctx.fillRect(0,0,w,h);
    if(transparent)ctx.clearRect(w*.4,h*.4,w*.2,h*.2);
    const blob=await new Promise(resolve=>source.toBlob(resolve,type));
    const transfer=new DataTransfer();
    transfer.items.add(new File([blob],'test.'+type.split('/')[1],{type}));
    const input=document.getElementById('zoomFile');input.files=transfer.files;
    input.dispatchEvent(new Event('change',{bubbles:true}));
    await wait(()=>document.querySelector('dialog .apply:not(:disabled)'));
    const dialog=document.querySelector('dialog'),canvas=dialog.querySelector('canvas'),slider=dialog.querySelector('input');
    const initial=canvas.toDataURL(),b=bounds(canvas);
    assert(Math.abs(b.width/b.height-w/h)<.015,'contain preserves original aspect ratio');
    assert(Math.abs((b.left+b.right)/2-149.5)<1&&Math.abs((b.top+b.bottom)/2-149.5)<1,'initial image centered');
    assert(Math.max(b.width,b.height)===300,'complete image fits initial square');
    assert(Number(slider.min)===.25&&Number(slider.max)>=4*Math.max(w/h,h/w),'dynamic range includes cover at extreme aspect ratios');
    slider.value=slider.max;slider.dispatchEvent(new Event('input',{bubbles:true}));await tick();
    assert(!white(pixel(canvas,0,0)),'zoom in can cover frame');
    slider.value='.25';slider.dispatchEvent(new Event('input',{bubbles:true}));await tick();
    const reduced=bounds(canvas);
    assert(Math.max(reduced.width,reduced.height)<=76,'substantial zoom out');
    assert(white(pixel(canvas,0,0)),'zoom out background is opaque white');
    const before=canvas.toDataURL();
    canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true}));
    canvas.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowDown',bubbles:true}));
    assert(canvas.toDataURL()!==before,'small image can move both axes');
    const moved=bounds(canvas);
    assert(moved.left>reduced.left&&moved.top>reduced.top,'small image moves right and down');
    canvas.setPointerCapture=()=>{};
    canvas.dispatchEvent(new PointerEvent('pointerdown',{pointerId:1,button:0,clientX:0,clientY:0}));
    canvas.dispatchEvent(new PointerEvent('pointermove',{pointerId:1,clientX:99999,clientY:99999}));
    canvas.dispatchEvent(new PointerEvent('pointerup',{pointerId:1}));
    assert(bounds(canvas).right>=0,'drag cannot hide image entirely');
    [...dialog.querySelectorAll('button')].find(button=>button.textContent==='Reset').click();await tick();
    assert(slider.value==='1'&&canvas.toDataURL()===initial,'Reset restores contain and centered X/Y');
    slider.value='.25';slider.dispatchEvent(new Event('input',{bubbles:true}));await tick();
    dialog.querySelector('.apply').click();
    await wait(()=>!document.querySelector('dialog'));
    await wait(()=>{try{return !!uploader.formData({}).get('image');}catch{return false;}});
    const file=uploader.formData({}).get('image'),bitmap=await createImageBitmap(file);
    assert(file.type==='image/webp'&&bitmap.width===512&&bitmap.height===512,'normalized WEBP');
    const output=document.createElement('canvas');output.width=512;output.height=512;
    output.getContext('2d').drawImage(bitmap,0,0);bitmap.close();
    const out=bounds(output);
    assert(Math.abs(out.width/out.height-w/h)<.02,'output is not stretched');
    assert(Math.max(out.width,out.height)<=130,'output retains zoom-out layout');
    assert(white(pixel(output,0,0)),'white padding encoded into output');
    if(transparent)assert(white(pixel(output,256,256)),'transparent PNG flattened on same background');
    uploader.reset(null,false);
  }
  await uploader.destroy();
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_component_script(script)

    def run_component_script(self, script):
        bundle = ROOT / "static" / "components" / "image-uploader"
        self.assertTrue((bundle / "image-uploader.js").is_file(), "Run npm.cmd run build in shared/image-components first.")
        page = (
            '<html><head><meta charset="utf-8">'
            '<link rel="stylesheet" href="/image-uploader.css">'
            '<script src="/image-uploader.js"></script></head><body>'
            '<span data-current-user-avatar data-user-id="1"></span>'
            '<div id="uploader"></div><div id="avatar"></div><pre id="result">pending</pre>'
            "<script>document.addEventListener('DOMContentLoaded',()=>{"
            "new MutationObserver(()=>{const value=document.getElementById('result').textContent;"
            "if(value==='pending')return;const request=new XMLHttpRequest();"
            "request.open('POST','/result');request.send(value);"
            "}).observe(document.getElementById('result'),{childList:true});"
            f"{script}"
            "});</script></body></html>"
        )
        finished = threading.Event()
        outcome = {}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                files = {"/image-uploader.js": ("text/javascript", bundle / "image-uploader.js"),
                         "/image-uploader.css": ("text/css", bundle / "image-uploader.css")}
                if self.path == "/":
                    content, kind = page.encode("utf-8"), "text/html; charset=utf-8"
                elif self.path in files:
                    kind, file = files[self.path]
                    content = file.read_bytes()
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.end_headers()
                self.wfile.write(content)

            def do_POST(self):
                if self.path != "/result":
                    self.send_error(404)
                    return
                outcome.update(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(204)
                self.end_headers()
                finished.set()

        temporary = tempfile.TemporaryDirectory(prefix="awsum-image-component-")
        with temporary as directory:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with subprocess.Popen(
                    [str(BROWSER), "--headless", "--disable-gpu", "--no-first-run",
                     f"--user-data-dir={Path(directory) / 'profile'}",
                     f"http://127.0.0.1:{server.server_port}/"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                ) as process:
                    try:
                        self.assertTrue(finished.wait(30), "Chromium did not complete the component checks.")
                    finally:
                        if os.name == "nt" and process.poll() is None:
                            stopped = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                                     stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=10)
                            if stopped.returncode and process.poll() is None:
                                process.terminate()
                        elif process.poll() is None:
                            process.terminate()
                        process.wait(timeout=10)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                for attempt in range(30):
                    try:
                        # Windows crash reporting can release profile handles just after browser exit.
                        temporary.cleanup()
                        break
                    except PermissionError:
                        if attempt == 29:
                            raise
                        time.sleep(0.1)
            self.assertTrue(outcome["ok"], outcome)
            self.assertEqual(outcome["networkCalls"], 0)

    def test_existing_logo_input_uses_common_adjuster_and_deferred_preview(self):
        script = r"""
const tick=()=>new Promise(r=>setTimeout(r,25));
const wait=async check=>{for(let n=0;n<200;n++){if(check())return;await tick();}throw Error('Timed out');};
const assert=(value,message)=>{if(!value)throw Error(message);};
let networkCalls=0;window.fetch=()=>{networkCalls++;throw Error('Unexpected request');};
(async()=>{
  const host=document.getElementById('uploader');
  host.innerHTML='<div class="modal"><input type="file"><img></div>';
  const input=host.querySelector('input'),preview=host.querySelector('img');
  const canvas=document.createElement('canvas');canvas.width=160;canvas.height=80;
  canvas.getContext('2d').fillRect(0,0,160,80);
  const saved=canvas.toDataURL();preview.src=saved;
  const blob=await new Promise(r=>canvas.toBlob(r,'image/png'));
  let applied=0;
  const binding=AwsumImages.bindImageInput(input,message=>{throw Error(message);},preview);
  input.addEventListener('awsumimageapply',()=>applied++);
  const select=()=>{const transfer=new DataTransfer();transfer.items.add(new File([blob],'logo.PNG',{type:'image/png'}));
    input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));};
  select();await wait(()=>document.querySelector('dialog .apply:not(:disabled)'));
  assert(document.querySelector('dialog h2').textContent==='Adjust Image','same adjuster title');
  let blocked=false;try{binding.pending();}catch{blocked=true;}
  assert(blocked,'busy parent save blocked');
  document.querySelector('dialog .apply').click();
  await wait(()=>!input.disabled);
  assert(applied===1&&binding.pending().name==='image.webp','same processor and deferred file');
  assert(preview.src.startsWith('blob:'),'local logo preview before Upload');
  const bitmap=await createImageBitmap(binding.pending());
  assert(bitmap.width===AwsumImages.imageConfig.width&&bitmap.height===AwsumImages.imageConfig.height,'shared config');
  bitmap.close();
  host.querySelector('.modal').dispatchEvent(new Event('hidden.bs.modal'));
  assert(!binding.pending()&&preview.src===saved,'parent Cancel restores image without a write');
  select();await wait(()=>document.querySelector('dialog .apply:not(:disabled)'));
  document.querySelector('dialog').dispatchEvent(new Event('cancel',{cancelable:true}));
  await wait(()=>!input.disabled);
  assert(applied===1&&!binding.pending()&&preview.src===saved,'adjust Cancel preserves original');
  document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls});
})().catch(error=>{document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack});});
"""
        self.run_component_script(script)


if __name__ == "__main__":
    unittest.main()
