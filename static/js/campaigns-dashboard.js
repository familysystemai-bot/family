/**
 * معاينة حية للحملة — لا يغيّر إرسال النموذج.
 */
(function () {
  'use strict';

  var DEMO_NAME = 'محمد';

  function qs(sel, root) {
    return (root || document).querySelector(sel);
  }

  function init() {
    var form = qs('#campaignCreateForm');
    if (!form) return;

    var titleIn = form.querySelector('[name="title"]');
    var msgIn = form.querySelector('[name="email_message"]');
    var imgIn = qs('#campaign_image_input');
    var previewTitle = qs('#mkt_preview_campaign_title');
    var previewText = qs('#mkt_preview_message');
    var previewImgWrap = qs('#mkt_preview_img_wrap');
    var fileNameEl = qs('#mkt_file_name');
    var imgObjectUrl = null;

    function setPreviewImage(file) {
      if (!previewImgWrap) return;
      if (imgObjectUrl) {
        URL.revokeObjectURL(imgObjectUrl);
        imgObjectUrl = null;
      }
      previewImgWrap.innerHTML = '';
      if (!file || !file.type || !file.type.startsWith('image/')) {
        previewImgWrap.innerHTML =
          '<span class="mkt-dash__preview-img--placeholder">ستظهر صورة الحملة هنا بعد الرفع</span>';
        return;
      }
      imgObjectUrl = URL.createObjectURL(file);
      var im = document.createElement('img');
      im.className = 'mkt-dash__preview-img';
      im.src = imgObjectUrl;
      im.alt = '';
      previewImgWrap.appendChild(im);
    }

    function sync() {
      var t = (titleIn && titleIn.value) ? titleIn.value.trim() : '';
      var m = (msgIn && msgIn.value) ? msgIn.value.trim() : '';

      if (previewTitle) {
        previewTitle.textContent = t || 'عنوان الحملة';
      }

      var greeting = 'هلا والله يا ' + DEMO_NAME + ' 👋';
      var body = m || 'اكتب نص الرسالة لمشاهدة المعاينة هنا…';
      var foot = '📍 العرض متوفر الآن';
      if (previewText) {
        previewText.textContent = greeting + '\n\n' + body + '\n\n' + foot;
      }
    }

    if (titleIn) titleIn.addEventListener('input', sync);
    if (titleIn) titleIn.addEventListener('change', sync);
    if (msgIn) msgIn.addEventListener('input', sync);
    if (msgIn) msgIn.addEventListener('change', sync);

    if (imgIn) {
      imgIn.addEventListener('change', function () {
        var f = imgIn.files && imgIn.files[0];
        if (fileNameEl) {
          fileNameEl.textContent = f ? f.name : '';
        }
        setPreviewImage(f);
      });
    }

    sync();
    setPreviewImage(null);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
