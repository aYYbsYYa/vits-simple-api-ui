import numpy as np
import torch

from bert_vits2 import commons
from bert_vits2 import utils as bert_vits2_utils
from bert_vits2.models import SynthesizerTrn
from bert_vits2.text import *
from bert_vits2.text.cleaner import clean_text
from utils import classify_language
from utils.sentence import sentence_split_and_markup, cut


class Bert_VITS2:
    def __init__(self, model, config, device=torch.device("cpu"), **kwargs):
        self.hps_ms = bert_vits2_utils.get_hparams_from_file(config)
        self.n_speakers = getattr(self.hps_ms.data, 'n_speakers', 0)
        self.speakers = [item[0] for item in
                         sorted(list(getattr(self.hps_ms.data, 'spk2id', {'0': 0}).items()), key=lambda x: x[1])]

        self.legacy = getattr(self.hps_ms.data, 'legacy', False)
        self.symbols = symbols_legacy if self.legacy else symbols
        self._symbol_to_id = {s: i for i, s in enumerate(self.symbols)}

        self.net_g = SynthesizerTrn(
            len(self.symbols),
            self.hps_ms.data.filter_length // 2 + 1,
            self.hps_ms.train.segment_size // self.hps_ms.data.hop_length,
            n_speakers=self.hps_ms.data.n_speakers,
            symbols=self.symbols,
            **self.hps_ms.model).to(device)
        _ = self.net_g.eval()
        self.device = device
        self.load_model(model)

    def load_model(self, model):
        bert_vits2_utils.load_checkpoint(model, self.net_g, None, skip_optimizer=True)

    def get_speakers(self):
        return self.speakers

    def get_text(self, text, language_str, hps):
        norm_text, phone, tone, word2ph = clean_text(text, language_str)
        phone, tone, language = cleaned_text_to_sequence(phone, tone, language_str, self._symbol_to_id)

        if hps.data.add_blank:
            phone = commons.intersperse(phone, 0)
            tone = commons.intersperse(tone, 0)
            language = commons.intersperse(language, 0)
            for i in range(len(word2ph)):
                word2ph[i] = word2ph[i] * 2
            word2ph[0] += 1
        bert = get_bert(norm_text, word2ph, language_str)
        del word2ph
        assert bert.shape[-1] == len(phone), phone

        if language_str == "zh":
            bert = bert
            ja_bert = torch.zeros(768, len(phone))
        elif language_str == "ja":
            ja_bert = bert
            bert = torch.zeros(1024, len(phone))
        else:
            bert = torch.zeros(1024, len(phone))
            ja_bert = torch.zeros(768, len(phone))
        assert bert.shape[-1] == len(
            phone
        ), f"Bert seq len {bert.shape[-1]} != {len(phone)}"
        phone = torch.LongTensor(phone)
        tone = torch.LongTensor(tone)
        language = torch.LongTensor(language)
        return bert, ja_bert, phone, tone, language

    def infer(self, text, lang, sdp_ratio, noise_scale, noise_scale_w, length_scale, sid):
        bert, ja_bert, phones, tones, lang_ids = self.get_text(text, lang, self.hps_ms)
        with torch.no_grad():
            x_tst = phones.to(self.device).unsqueeze(0)
            tones = tones.to(self.device).unsqueeze(0)
            lang_ids = lang_ids.to(self.device).unsqueeze(0)
            bert = bert.to(self.device).unsqueeze(0)
            ja_bert = ja_bert.to(self.device).unsqueeze(0)
            x_tst_lengths = torch.LongTensor([phones.size(0)]).to(self.device)
            speakers = torch.LongTensor([int(sid)]).to(self.device)
            audio = self.net_g.infer(x_tst, x_tst_lengths, speakers, tones, lang_ids, bert, ja_bert, sdp_ratio=sdp_ratio
                                     , noise_scale=noise_scale, noise_scale_w=noise_scale_w, length_scale=length_scale)[
                0][0, 0].data.cpu().float().numpy()

        torch.cuda.empty_cache()
        return audio

    def get_audio(self, voice, auto_break=False):
        text = voice.get("text", None)
        lang = voice.get("lang", "auto")
        sdp_ratio = voice.get("sdp_ratio", 0.2)
        noise_scale = voice.get("noise", 0.5)
        noise_scale_w = voice.get("noisew", 0.6)
        length_scale = voice.get("length", 1)
        sid = voice.get("id", 0)
        max = voice.get("max", 50)
        # sentence_list = sentence_split_and_markup(text, max, "ZH", ["zh"])
        if lang == "auto":
            lang = classify_language(text)
        sentence_list = cut(text, max)
        audios = []
        for sentence in sentence_list:
            audio = self.infer(sentence, lang, sdp_ratio, noise_scale, noise_scale_w, length_scale, sid)
            audios.append(audio)
        audio = np.concatenate(audios)
        return audio
