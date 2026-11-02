"""Organization logos reuse the existing User image component and atomic service."""
import json
import re
import unittest
from unittest.mock import patch

from sqlalchemy.exc import SQLAlchemyError

from backend.models_admin.store import Store
from backend.models_admin.agency import Agency
from backend.models_admin.agency_type import AgencyType
from backend.models_admin.merchant import Merchant
from backend.models_admin.business_contact import BusinessContact
from backend.models_admin.business_type import BusinessType
from backend.routers import platform_store as routes
from backend.services import images
from tools import test_image_components as browser, test_master_images


class StoreLogoTests(unittest.TestCase):
    setUp = test_master_images.MasterImageTests.setUp
    sign_in = test_master_images.MasterImageTests.sign_in

    def save(self, data=None, *, remove=False, changes=None):
        return self.client.put(
            "/platform/store/2/with-image",
            data={"payload": json.dumps({"store_name": "Saved Store", **(changes or {})}),
                  "remove_image": str(remove).lower()},
            files={"image": ("original.png", data, "image/png")} if data is not None else None,
        )

    def test_replace_remove_cancel_save_and_existing_paths(self):
        with patch.object(images, "process_image", wraps=images.process_image) as processor:
            created = self.save(self.blue)
        self.assertEqual(created.status_code, 200, created.text)
        processor.assert_called_once_with(self.blue)
        item = created.json()
        self.assertEqual(item["image_path"], "/merchant/CLT11001/store/STR00002/images/logo/logo.webp")
        file = images.resolve_image_file(item["image_path"])
        original = file.read_bytes()
        for endpoint in ("/platform/store/2", "/platform/merchant/1/stores/2"):
            result = self.client.get(endpoint)
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()["image_url"], item["image_url"])
        listed = self.client.get("/platform/stores").json()
        self.assertEqual(next(row for row in listed if row["store_id"] == 2)["image_path"], item["image_path"])
        # Local Cancel sends no mutation. Reload retains both file and DB reference.
        self.assertEqual(self.client.get("/platform/store/2").json()["image_path"], item["image_path"])
        self.assertEqual(file.read_bytes(), original)
        unchanged = self.save(changes={"phone": "555-1111"})
        self.assertEqual(unchanged.status_code, 200, unchanged.text)
        self.assertEqual(file.read_bytes(), original)
        replaced = self.save(self.red)
        self.assertEqual(replaced.status_code, 200, replaced.text)
        self.assertEqual(replaced.json()["image_path"], item["image_path"])
        self.assertNotEqual(file.read_bytes(), original)
        removed = self.save(remove=True)
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertIsNone(removed.json()["image_path"])
        self.assertFalse(file.exists())
        self.db.expire_all()
        self.assertEqual(self.db.get(Store, 2).c_phone, "555-1111")

    def test_create_save_uses_existing_store_creation_and_owner_path(self):
        payload = {"store_name": "New Store", "merchant_id": 1, "installed_by_agency_id": 1}
        # Only the existing PostgreSQL ID/code generators and post-commit directory
        # initialization are substituted in this isolated SQLite fixture.
        with patch.object(routes, "_generate_merchant_store_code", return_value=("ST000001", 1)), \
                patch.object(routes, "get_next_merchant_store_id", return_value=3), \
                patch.object(routes.AwsumStorageService, "initialize_after_registration",
                             return_value={"storage_initialized": True}):
            saved = self.client.post("/platform/store/with-image", data={"payload": json.dumps(payload)},
                                     files={"image": ("original.png", self.blue, "image/png")})
        self.assertEqual(saved.status_code, 200, saved.text)
        item = saved.json()
        self.assertEqual((item["store_id"], item["store_code"]), (3, "ST000001"))
        self.assertEqual(item["image_path"], "/merchant/CLT11001/store/ST000001/images/logo/logo.webp")
        self.assertTrue(images.resolve_image_file(item["image_path"]).is_file())
        self.db.expire_all()
        record = self.db.get(Store, 3)
        self.assertEqual((record.c_store_name, record.i_merchant_id, record.i_installed_by_agency_id),
                         ("New Store", 1, 1))

    def test_invalid_upload_and_failed_commit_preserve_image_and_store(self):
        created = self.save(self.blue).json()
        file = images.resolve_image_file(created["image_path"])
        original = file.read_bytes()
        invalid = self.save(b"not an image", changes={"store_name": "Must Not Save"})
        self.assertEqual(invalid.status_code, 400, invalid.text)
        self.db.expire_all()
        self.assertEqual(self.db.get(Store, 2).c_store_name, "Saved Store")
        self.assertEqual(file.read_bytes(), original)
        for remove in (False, True):
            with self.subTest(remove=remove), self.factory() as db:
                row = db.get(Store, 2)
                actor = db.get(routes.PlatformUser, 1)
                actor.login_role = 1
                request = self.client.build_request("PUT", "/platform/store/2/with-image")
                with patch.object(routes, "get_current_user", return_value=actor), \
                        patch.object(db, "commit", side_effect=SQLAlchemyError("Commit failed")):
                    with self.assertRaises(SQLAlchemyError):
                        routes._update_store(2, {"store_name": "Must Roll Back"}, request, db,
                                             image_data=None if remove else self.red, remove_image=remove)
                self.assertEqual(file.read_bytes(), original)
                self.assertEqual(db.get(Store, 2).c_logo_url, created["image_path"])
                self.assertEqual(db.get(Store, 2).c_store_name, "Saved Store")

    def test_legacy_logo_remains_until_successful_save_and_json_api_still_works(self):
        legacy = self.root / "static" / "images" / "brands" / "clients" / "CLT11001" / "stores" / "STR00002" / "logo.png"
        legacy.parent.mkdir(parents=True)
        legacy.write_bytes(self.blue)
        with patch.object(images, "STATIC_ROOT", self.root / "static"):
            legacy_path = "/static/images/brands/clients/CLT11001/stores/STR00002/logo.png"
            self.db.get(Store, 2).c_logo_url = legacy_path
            self.db.commit()
            response = self.client.put("/platform/store/2", json={"store_name": "JSON unchanged"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(legacy.is_file())
            self.assertEqual(self.client.get("/platform/store/2").json()["image_path"], legacy_path)
            saved = self.save(self.red)
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["image_path"], "/merchant/CLT11001/store/STR00002/images/logo/logo.webp")
            self.assertEqual(legacy.read_bytes(), self.blue)


class OrganizationLogoTests(unittest.TestCase):
    setUp = test_master_images.MasterImageTests.setUp
    sign_in = test_master_images.MasterImageTests.sign_in

    def prepare_agency(self):
        self.db.add(AgencyType(i_agency_type_id=1, c_agency_type_code="TEST", c_agency_type_name="Test"))
        row = self.db.get(Agency, 1)
        row.i_agency_type_id, row.c_status = 1, "inactive"
        self.db.commit()

    def test_agency_and_merchant_add_edit_replace_remove_with_same_service(self):
        self.prepare_agency()
        self.db.add(BusinessType(i_business_type_id=1, c_business_code="TUXEDO_RENTAL",
                                 c_name="Tuxedo Rental", c_status="active"))
        self.db.commit()
        for endpoint, model, context, payload, expected in (
            ("agency", Agency, "agency", {"company_name": "New Agency", "agency_type_id": 1, "status": "inactive"}, None),
            ("merchant", Merchant, "merchant", {"merchant_name": "New Merchant", "primary_agency_id": 1, "business_type": "Tuxedo Rental"}, "MER26A7K3"),
        ):
            with self.subTest(endpoint=endpoint), \
                    patch.object(routes, "_generate_next_merchant_code", return_value="MER26A7K3"), \
                    patch.object(routes.AwsumStorageService, "initialize_after_registration",
                                 return_value={"storage_initialized": True}), \
                    patch.object(images, "process_image", wraps=images.process_image) as processor:
                created = self.client.post(f"/platform/{endpoint}/with-image",
                                           data={"payload": json.dumps(payload)},
                                           files={"image": ("camera.png", self.blue, "image/png")})
                self.assertEqual(created.status_code, 200, created.text)
                processor.assert_called_once_with(self.blue)
                item = created.json()
                if endpoint == "agency":
                    expected = item["agency_code"]
                    self.assertRegex(expected, r"^AGT[0-9]{2}[A-HJ-NP-Z2-9]{4}$")
                path = f"/{context}/{expected}/images/logo/logo.webp"
                self.assertEqual(item["image_path"], path)
                file = images.resolve_image_file(path)
                original = file.read_bytes()
                identity = item["id"]
                updates = {"memo": "Updated without photo"}
                unchanged = self.client.put(f"/platform/{endpoint}/{identity}/with-image",
                                            data={"payload": json.dumps(updates)})
                self.assertEqual(unchanged.status_code, 200, unchanged.text)
                self.assertEqual(file.read_bytes(), original)
                self.assertEqual(self.db.get(model, identity).c_memo, updates["memo"])
                replaced = self.client.put(f"/platform/{endpoint}/{identity}/with-image",
                                           data={"payload": "{}"},
                                           files={"image": ("replacement.png", self.red, "image/png")})
                self.assertEqual(replaced.status_code, 200, replaced.text)
                self.assertEqual(replaced.json()["image_path"], path)
                self.assertNotEqual(file.read_bytes(), original)
                listed = self.client.get("/platform/agencies" if endpoint == "agency" else "/platform/merchants")
                self.assertEqual(listed.status_code, 200, listed.text)
                self.assertEqual(next(row for row in listed.json() if row["id"] == identity)["image_url"],
                                 replaced.json()["image_url"])
                removed = self.client.put(f"/platform/{endpoint}/{identity}/with-image",
                                          data={"payload": "{}", "remove_image": "true"})
                self.assertEqual(removed.status_code, 200, removed.text)
                self.assertIsNone(removed.json()["image_path"])
                self.assertFalse(file.exists())

    def test_invalid_images_and_failed_saves_preserve_both_fields_and_files(self):
        self.prepare_agency()
        for endpoint, model in (("agency", Agency), ("merchant", Merchant)):
            with self.subTest(endpoint=endpoint):
                initial = self.client.put(f"/platform/{endpoint}/1/with-image", data={"payload": "{}"},
                                          files={"image": ("initial.png", self.blue, "image/png")})
                self.assertEqual(initial.status_code, 200, initial.text)
                item = initial.json()
                file = images.resolve_image_file(item["image_path"])
                original = file.read_bytes()
                invalid = self.client.put(f"/platform/{endpoint}/1/with-image",
                                          data={"payload": '{"memo":"Must Not Save"}'},
                                          files={"image": ("broken.png", b"broken", "image/png")})
                self.assertEqual(invalid.status_code, 400, invalid.text)
                for remove in (False, True):
                    with self.factory() as db:
                        actor = db.get(routes.PlatformUser, 1)
                        actor.login_role = 1
                        function = routes._update_agent if endpoint == "agency" else routes._update_merchant
                        old_memo = db.get(model, 1).c_memo
                        with patch.object(routes, "get_current_user", return_value=actor), \
                                patch.object(db, "commit", side_effect=SQLAlchemyError("Commit failed")):
                            with self.assertRaises((SQLAlchemyError, routes.HTTPException)):
                                function(1, {"memo": "Must Roll Back"}, self.client.build_request("PUT", "/"),
                                         db, image_data=None if remove else self.red, remove_image=remove)
                        self.assertEqual(file.read_bytes(), original)
                        self.assertEqual(db.get(model, 1).c_logo_url, item["image_path"])
                        self.assertEqual(db.get(model, 1).c_memo, old_memo)
                self.assertEqual(self.client.put(f"/platform/{endpoint}/1/with-image",
                                                 data={"payload": "{}"},
                                                 headers={"Origin": "https://foreign.example.test"}).status_code, 403)
        self.sign_in(3)
        for endpoint in ("agency", "merchant"):
            self.assertEqual(self.client.put(f"/platform/{endpoint}/1/with-image",
                                             data={"payload": "{}"}).status_code, 403)

    def test_agency_multipart_save_preserves_existing_activation(self):
        self.prepare_agency()
        self.db.add(routes.Role(i_role_id=99, c_name=routes.AGENCY_ADMIN_ROLE_NAME, c_status="active"))
        self.db.add(BusinessContact(
            c_owner_type="AGENCY", i_owner_id=1, c_contact_role="PRIMARY",
            c_first_name="Logo", c_last_name="Contact", c_email="logo@example.test",
            c_phone="703-855-5858", b_is_primary=True, c_status="active",
        ))
        self.db.commit()
        with patch.object(routes, "send_configured_email") as smtp:
            response = self.client.put("/platform/agency/1/with-image",
                                       data={"payload": '{"status":"active"}'},
                                       files={"image": ("logo.png", self.blue, "image/png")})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["agency_user_created"])
            self.assertEqual(response.json()["temporary_password"], "Awsum@5858")
            smtp.assert_not_called()
        self.db.expire_all()
        account = self.db.query(routes.PlatformUser).filter_by(i_agency_id=1).one()
        password_hash = account.c_password
        response = self.client.put("/platform/agency/1/with-image",
                                   data={"payload": '{"status":"active"}', "remove_image": "true"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json().get("agency_user_created", False))
        self.db.expire_all()
        self.assertEqual(self.db.query(routes.PlatformUser).filter_by(i_agency_id=1).count(), 1)
        self.assertEqual(account.c_password, password_hash)


@unittest.skipUnless(browser.BROWSER.is_file(), "Set AWSUM_REPORT_CHROME to Chrome/Edge.")
class StoreLogoBrowserTests(unittest.TestCase):
    run_browser = browser.SharedImageComponentTests.run_component_script

    def test_both_store_forms_use_common_pending_preview_cancel_and_save(self):
        source = (browser.ROOT / "templates" / "platform" / "master_management.html").read_text(encoding="utf-8")
        for old_id in ("btnUploadStoreLogo", "btnResetStoreLogo", "btnUploadMerchantStoreLogo",
                       "btnResetMerchantStoreLogo", "btnBrowseMerchantStoreLogoFile"):
            self.assertNotIn(old_id, source)
        functions = "\n".join(re.search(
            rf"  (?:async )?function {name}\([^\n]*\) \{{.*?\n  \}}", source, re.DOTALL,
        ).group() for name in (
            "getStoreLogoUploader", "getOrganizationLogoUploader", "refreshOrgLogoPreview",
            "refreshStoreLogoPreview", "refreshMerchantStoreModalLogoPreview",
            "snapshotFormState", "isFormDirty", "getItemUrl", "saveItem", "saveMerchantLinkedStore",
        ))
        setup = r"""
let activeModule='store';
const apiBase='/platform',config={fields:[],get endpoint(){return activeModule;}},isListCentricModule=true;
const selectedMasterId={value:'2'},formBaselineSnapshot={id:'2'};
let savingStoreImage=false,agencyModalMode='edit',lastSavedId=null,selectedMerchantStoreId=2,suppressMerchantStoreRowClickUntil=0;
const getFormData=()=>({store_name:'Changed Store'}),collectMerchantStorePayload=getFormData;
const usesPremiumModal=()=>true;
const getSelectedMerchantContext=()=>({merchantId:1,isReadyForStoreRegistration:true});
const validateStoreForm=()=>[],jumpToStoreErrorTab=()=>{},closeMasterEditModal=()=>{};
const validateAgencyForm=validateStoreForm,validateMerchantForm=validateStoreForm;
let error='',requests=[],failNext=false;
const showMasterModalError=message=>{error=message;},parseApiError=value=>value;
const setBaselineSnapshot=()=>{},updateListCentricActionButtons=()=>{},notifyStorageWarning=()=>{};
const agencyRegistrationPasswords=new Map();
const showCenteredConfirm=async()=>true;
window.alert=message=>{error=message;};
window.fetch=async(path,options)=>{
 requests.push({path,method:options.method,body:options.body});
 if(failNext){failNext=false;return new Response('Save failed',{status:409});}
 return new Response(JSON.stringify({id:2,store_id:2}));
};
const canvas=document.createElement('canvas');canvas.width=20;canvas.height=30;
canvas.getContext('2d').fillRect(0,0,20,30);
const saved=canvas.toDataURL(),item={image_url:saved,image_path:'/existing-logo.webp'};
const loadList=async()=>refreshStoreLogoPreview(item),loadMerchantStoresForSelectedMerchant=async()=>{};
const selectedOriginalRecord=item;
const resetMerchantStoreEditor=()=>refreshMerchantStoreModalLogoPreview();
const setMerchantStoreFormVisible=()=>{};
document.getElementById('uploader').innerHTML='<div class="modal" id="master"><div id="storeLogoUploader"></div><div id="agencyLogoUploader"></div><div id="merchantLogoUploader"></div><div id="merchantLogoUploader"></div></div><div class="modal" id="linked"><div id="merchantStoreLogoUploader"></div></div>';
"""
        setup += functions
        setup += r"""
const assert=(ok,message)=>{if(!ok)throw Error(message);};
const tick=()=>new Promise(resolve=>setTimeout(resolve,25));
const wait=async check=>{for(let i=0;i<200;i++){if(check())return;await tick();}throw Error('Image processing timeout');};
(async()=>{
 for(const module of ['store','agency','merchant']){
 activeModule=module;
 for(const linked of (module==='store'?[false,true]:[false])){
  const prefix=linked?'merchantStoreLogo':module==='merchant'?'merchantLogo':module+'Logo';
  const uploader=linked||module==='store'?getStoreLogoUploader(linked):getOrganizationLogoUploader();
  const refresh=()=>linked?refreshMerchantStoreModalLogoPreview(item):refreshStoreLogoPreview(item);
  const save=()=>linked?saveMerchantLinkedStore():saveItem();
  const modal=document.getElementById(linked?'linked':'master');
  refresh();await tick();
  assert(document.getElementById(prefix+'Preview').src===saved,'existing logo shown');
  document.getElementById(prefix+'Remove').click();await tick();
  assert(requests.length===0,'Remove is local before Save');
  modal.dispatchEvent(new Event('hidden.bs.modal'));refresh();await tick();
  assert(uploader.formData({}).get('remove_image')==='false','Cancel discards removal');
  const input=document.getElementById(prefix+'File');
  const select=file=>{const transfer=new DataTransfer();transfer.items.add(file);input.files=transfer.files;input.dispatchEvent(new Event('change',{bubbles:true}));};
  select(new File(['<svg/>'],'invalid.svg',{type:'image/svg+xml'}));await tick();
  await save();assert(requests.length===0&&error,'same validation blocks Save');
  refresh();error='';
  const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/png'));
  select(new File([blob],'logo.png',{type:'image/png'}));await tick();
  await wait(()=>document.querySelector('dialog .apply:not(:disabled)'));
  await save();assert(requests.length===0&&error.includes('Apply or cancel'),'unconfirmed crop blocks Save');
  document.querySelector('dialog').dispatchEvent(new Event('cancel',{cancelable:true}));await tick();
  assert(!uploader.formData({}).has('image'),'crop Cancel preserves saved logo');
  select(new File([blob],'logo.png',{type:'image/png'}));
  await wait(()=>document.querySelector('dialog .apply:not(:disabled)'));
  document.querySelector('dialog .apply').click();
  await wait(()=>{try{return uploader.formData({}).has('image');}catch{return false;}});
  assert(requests.length===0,'selection/preview never uploads');
  modal.dispatchEvent(new Event('hidden.bs.modal'));refresh();await tick();
  assert(!uploader.formData({}).has('image'),'modal Cancel discards replacement');
  select(new File([blob],'logo.png',{type:'image/png'}));
  await wait(()=>document.querySelector('dialog .apply:not(:disabled)'));
  document.querySelector('dialog .apply').click();
  await wait(()=>{try{return uploader.formData({}).has('image');}catch{return false;}});
  failNext=true;await save();await tick();
  assert(requests.length===1&&uploader.formData({}).has('image'),'failed Save retains pending replacement');
  await save();await tick();
  assert(requests.length===2,'retry saves once');
  const request=requests.at(-1);
  const endpoint=module==='agency'?'agency':module==='store'?'store':'merchant';
  assert(request.path==='/platform/'+endpoint+'/2/with-image'&&request.method==='PUT','shared multipart adapter');
  assert(request.body.get('image') instanceof File&&request.body.get('remove_image')==='false','common multipart replacement');
  assert(JSON.parse(request.body.get('payload')).store_name==='Changed Store','existing Store fields saved together');
  refresh();await tick();requests=[];
  document.getElementById(prefix+'Remove').click();await tick();
  if(!linked)assert(isFormDirty(),'image-only change enables Save');
  await save();await tick();
  assert(requests.length===1&&requests[0].body.get('remove_image')==='true'&&!requests[0].body.has('image'),'Remove committed only by Save');
  requests=[];
  if(!linked){
    selectedMasterId.value='';agencyModalMode='create';
    select(new File([blob],'logo.png',{type:'image/png'}));
    await wait(()=>document.querySelector('dialog .apply:not(:disabled)'));
    document.querySelector('dialog .apply').click();
    await wait(()=>{try{return uploader.formData({}).has('image');}catch{return false;}});
    assert(!requests.length,'Add image remains local before Save');
    await save();await tick();
    assert(requests.length===1&&requests[0].method==='POST'&&requests[0].path==='/platform/'+endpoint+'/with-image','Add uses same common uploader');
    assert(requests[0].body.get('image') instanceof File,'Add saves pending common image');
    selectedMasterId.value='2';agencyModalMode='edit';requests=[];
  }
 }
 }
 document.getElementById('result').textContent=JSON.stringify({ok:true,networkCalls:0});
})().catch(error=>document.getElementById('result').textContent=JSON.stringify({ok:false,error:String(error),stack:error.stack}));
"""
        self.run_browser(setup)
