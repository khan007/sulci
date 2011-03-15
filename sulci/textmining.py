#!/usr/bin/env python
# -*- coding:Utf-8 -*-

import os
import re
import urllib2
import time, datetime
import math

from collections import defaultdict
from operator import itemgetter
from GenericCache.GenericCache import GenericCache
from GenericCache.decorators import cached

from utils import uniqify, sort, product, log
from stopwords import stop_words, usual_words
from textminingutils import lev, normalize_text, words_occurrences
from base import RetrievableObject, Sample, Token, TextManager
from pos_tagger import PosTagger
from lexicon import Lexicon
from thesaurus import Trigger
from lemmatizer import Lemmatizer

#Cache
cache = GenericCache()

class SemanticalTagger(TextManager):
    """
    Main class.
    """
    def __init__(self, text, thesaurus, pos_tagger=None, lemmatizer=None):
        self.thesaurus = thesaurus
        self._raw_text = text
        self.normalized_text = normalize_text(text)
        self.samples = []
        self.keyentities = []
        self.postagger = pos_tagger or PosTagger(lexicon=Lexicon())
        self.lemmatizer = lemmatizer or Lemmatizer()
        self.make()

    def __iter__(self):
        return self.words.__iter__()

    def __len__(self):
        return len(self.words)

    def make(self):
        """
        Text is expected to be tokenized.
        And filtered ?
        """
        self.samples, self.tokens = self.instantiate_text(self.tokenize(self.normalized_text))
        self.postagger.tag_all(self.tokens)
        self.create_stemm()
        self.make_keyentities()
    
    def create_stemm(self):
        for tkn in self.tokens:
            self.lemmatizer.do(tkn)
            #We don't take the sg or pl in the tag name
            stm, created = Stemm.get_or_create((unicode(tkn.lemme), tkn.tag.split(u":")[0]), self, original=unicode(tkn.lemme), text=self)
            stm.occurrences.append(tkn)
            tkn.stemm = stm
    
    @property
    @cached(cache)
    def medium_word_count(self):
#        print "*********************", 1.0 * len(self.words) / len(set(self.distinct_words()))
        return 1.0 * len(self.words) / len(set(self.distinct_words()))

    @property
    def words(self):
        return [w for s in self.samples for w in s]

    @property
    def meaning_words(self):
        return [w for s in self.samples for w in s if w.has_meaning()]

    @property
    def stemms(self):
        return uniqify([t.stemm for t in self.meaning_words], lambda x: x.id)

    def words_count(self):
        """
        Return the number of words in the text.
        """
        return sum([len(s) for s in self.samples])

    def meaning_words_count(self):
        """
        Return the number of words in the text.
        """
        return sum([s.meaning_words_count() for s in self.samples])

    def distinct_words(self):
        return uniqify(self.words, lambda x: x.original)

    def distincts_meaning_words(self):
        return uniqify(self.meaning_words, lambda x: x.original)

    def frequents_stemms(self, min_count=3):
        #Min_count may be proportionnal to text length...
#        print self.stemms
        candidates = [s for s in self.stemms if s.has_interest_alone()]
#        print candidates
        return sort(candidates, "count", reverse=True)

    def ngrams(self, min_length = 2, max_length = 15, min_count = 2):
        final = {}
    #    sentence = tuple(sentences[0])
        for idxs, sentence in enumerate(self.samples):
            sentence = tuple(sentence)
            for begin in range(0,len(sentence)):
                id_max = min(len(sentence) + 1, begin + max_length + 1)
                for end in range(begin + min_length, id_max):
                    g = sentence[begin:end]
                    #We make the comparison on stemmes
                    idxg = tuple([w.stemm for w in g])
                    if not g[0].has_meaning() or not g[len(g)-1].has_meaning():
                        continue#continuing just this for loop. Good ?
#                    if "projet" in g: log(g, RED)
#                    if g[1].original == "Bourreau" and len(g) == 2: print g
                    if not idxg in final:
                        final[idxg] = {"count": 1, "stemms": [t.stemm for t in g]}
                    else:
                        final[idxg]["count"] += 1
#                        if g[1].original == "Bourreau" and len(g) == 2: print "yet", idxg, final[idxg]['stemms'], final[idxg]["count"]
#        return final
#        pouet = sorted([u" ".join([s.main_occurrence.original for s in v["stemms"]]) for k, v in final.iteritems()])
#        for ppouet in pouet:
#            print ppouet
        return sorted([(v["stemms"], v["count"]) for k, v in final.iteritems() if self.filter_ngram(v)], key=itemgetter(1), reverse=True)

    def filter_ngram(self, candidate):
        """
        Here we try to keep the right ngrams to make keyentities.
        """
        return candidate["count"] >= 2 \
               or all([s.istitle() for s in candidate["stemms"]]) \
               or False

    def make_keyentities(self, min_length = 2, max_length = 10, min_count = 2):
        #From ngrams
        keyentities = []
        candidates = self.ngrams()
        #Candidates are tuples : (ngram, ngram_score)
        log([[unicode(s) for s in c[0]] for c in candidates], "CYAN")
        for candidate in candidates:
            kp, created = KeyEntity.get_or_create([unicode(s.main_occurrence) for s in candidate[0]],
                                                  self,
                                                  stemms=candidate[0], 
                                                  count=candidate[1],
                                                  text=self)
            keyentities.append(kp)
        #From frequency
        candidates = self.frequents_stemms()
        log([unicode(c) for c in candidates], "MAGENTA")
        for candidate in candidates:
            kp, created = KeyEntity.get_or_create([unicode(candidate.main_occurrence)], 
                                                  self,
                                                  stemms=[candidate], 
                                                  count=candidate.count,
                                                  text=self)
            keyentities.append(kp)
        #If a KeyEntity is contained in an other (same stemms in same place) longuer
        #delete the one with the smaller confidence, or the shortest if same confidence
        #We have to begin from the shortest ones
        log(u"Deduplicating keyentities", "WHITE")
        tmp_keyentities = sorted(keyentities, key=lambda kp: len(kp))
        log([unicode(kp) for kp in tmp_keyentities], "GRAY")
        for idx, one in enumerate(tmp_keyentities):
            for two in tmp_keyentities[idx+1:]:
                if one in keyentities and two in keyentities:
                    if one.is_duplicate(two):
                        log(u"%s is duplicate %s" % (unicode(one), unicode(two)), "MAGENTA")
                        if one > two:#and not two.is_strong()
                            log(u"%s will be deleted" % unicode(two), "RED")
                            keyentities.remove(two)
                        elif two > one:
                            log(u"%s will be deleted" % unicode(one), "RED")
                            keyentities.remove(one)
                        else:
                            log(u"Equal, no deletion")
        self.keyentities = keyentities
    
    def keyentities_for_trainer(self):
        return sorted(self.keyentities, key=lambda kp: kp.frequency_relative_pmi_confidence * kp._confidences["pos"], reverse=True)[:20]
    
    @property
    def descriptors(self):
        """
        Final descriptors for the text.
        """
        #Loading triggers...
        self.thesaurus.triggers
        self._scored_descriptors = set()
        total_score = 0
        for kp in self.keyentities:
            t, created = Trigger.get_or_create(unicode(kp), self.thesaurus, parent=self.thesaurus, original=unicode(kp))
            if not created:
#                log(u"%s => (%s)" % (repr(kp), unicode(t)), "YELLOW")
                for d in t:
                    if t[d] > 0:
                        if not d in self._scored_descriptors:
                            self._scored_descriptors.add(d)
                            d.score = 0
                        d.score += (t[d] / t.max_score) * kp.trigger_score
            total_score += kp.trigger_score
        for d in self._scored_descriptors:
            d.score = d.score / total_score * 100.0
        #This also means that only the descriptors triggered up to this min 
        #will be considered by trainer.
        min_score = 1
        return [d for d in sorted(self._scored_descriptors, key=lambda d: d.score, reverse=True) if d.score > min_score]
    
    
    def debug(self):
        log("Normalized text", "WHITE")
        log(self.normalized_text, "WHITE")
        log("Number of words", "WHITE")
        log(self.words_count(), "GRAY")
        log("Number of meaning words", "WHITE")
        log(self.meaning_words_count(), "GRAY")
        log("Number of differents words", "WHITE")
        log(len(self.distinct_words()), "GRAY")
        log("Frequents stemms", "WHITE")
        log([(unicode(s), s.count) for s in self.frequents_stemms()], "GRAY")
        log("Lexical diversity", "WHITE")
        log(1.0 * len(self.words) / len(set(self.distinct_words())), "GRAY")
        log("Tagged words", "WHITE")
        log([(unicode(t), t.tag) for t in self.tokens], "GRAY")
        log("Sentences", "WHITE")
        for sample in self.samples:
            log(sample, "GRAY")
#        log("Ngrams", "WHITE")
#        log(self.ngrams(), GRAY)
#        log("Thesaurus", "WHITE")
#        for kp in self.keyentities:
#            if kp in self.thesaurus:
#                log(u"%s in thesaurus => %s" % (unicode(kp), self.thesaurus[kp]), "BLUE")
        log("Final keyentities", "WHITE")
        for kp in sorted(self.keyentities, key=lambda kp: kp.keyconcept_confidence):
            log(u"%s (%f)" % (unicode(kp), kp.confidence), "YELLOW")
#            if kp.collocation_confidence > 1:
#                log(u"Collocation confidence => %f" % kp.collocation_confidence, "BLUE")
##                    print self.thesaurus[kp].id, self.thesaurus[kp].line
#            if kp.keyconcept_confidence > 0.01:
#                log(u"Keyconcept confidence (%f)" % kp.keyconcept_confidence, "CYAN")
#            if kp.descriptor is not None:
#                log(u"%s in thesaurus => %s" % (unicode(kp), unicode(kp.descriptor)), "MAGENTA")
            log(kp._confidences, "GRAY")
        log(u"Keyentities by keyconcept_confidence", "BLUE", True)
        for kp in sorted(self.keyentities, key=lambda kp: kp.keyconcept_confidence, reverse=True)[:10]:
            log(u"%s (%f)" % (unicode(kp), kp.keyconcept_confidence), "YELLOW")
        log(u"Keyentities by statistical_mutual_information_confidence", "BLUE", True)
        for kp in sorted(self.keyentities, key=lambda kp: kp._confidences["statistical_mutual_information"], reverse=True)[:10]:
            log(u"%s (%f)" % (unicode(kp), kp._confidences["statistical_mutual_information"]), "YELLOW")
        log(u"Keyentities by pos_confidence", "BLUE", True)
        for kp in sorted(self.keyentities, key=lambda kp: kp._confidences["pos"], reverse=True)[:10]:
            log(u"%s (%f)" % (unicode(kp), kp._confidences["pos"]), "YELLOW")
#        log(u"Keyentities by thesaurus_confidence", "BLUE", True)
#        for kp in sorted((kp for kp in self.keyentities if kp.descriptor is not None), key=lambda kp: kp._confidences["thesaurus"], reverse=True):
#            log(u"%s (%s)" % (unicode(kp), unicode(kp.descriptor)), "YELLOW")
        log(u"Keyentities by frequency_relative_pmi_confidence", "BLUE", True)
        for kp in sorted(self.keyentities, key=lambda kp: kp.frequency_relative_pmi_confidence, reverse=True)[:10]:
            log(u"%s (%f)" % (unicode(kp), kp.frequency_relative_pmi_confidence), "YELLOW")
        log(u"Keyentities by keyconcept_confidence * pos confidence", "BLUE", True)
        for kp in sorted(self.keyentities, key=lambda kp: kp.keyconcept_confidence * kp._confidences["pos"], reverse=True)[:10]:
            log(u"%s (%f)" % (unicode(kp), kp.keyconcept_confidence * kp._confidences["pos"]), "YELLOW")
        log(u"Keyentities by nrelative * pos confidence", "BLUE", True)
        for kp in sorted(self.keyentities, key=lambda kp: kp.trigger_score, reverse=True)[:20]:
            log(u"%s (%f)" % (unicode(kp), kp.trigger_score), "YELLOW")
        log(u"Keyentities from triggers", "BLUE", True)
        #Loading...
        self.thesaurus.triggers
        scored_descriptors = defaultdict(float)
        for kp in self.keyentities:
            t, created = Trigger.get_or_create(unicode(kp), self.thesaurus, parent=self.thesaurus, original=unicode(kp))
            if not created:
                log(u"%s => (%s)" % (repr(kp), unicode(t)), "YELLOW")
                for d in sorted(t._descriptors, key=lambda ds: t._descriptors[ds], reverse=True):
                    log(u"%s %f" % (unicode(d), t._descriptors[d] / t.max_score * 100), "CYAN")
#        log(u"Scored descriptors", "YELLOW", True)
#        for d, v in sorted(scored_descriptors.iteritems(), key=itemgetter(1), reverse=True):
#            if scored_descriptors[d] > 0.001:#Test
#                log(u"%s %f" % (unicode(d), scored_descriptors[d]), "WHITE")
        log(u"Scored descriptors", "YELLOW", True)
        for d in self.descriptors:
            log(u"%s %f" % (unicode(d), d.score), "WHITE")


class KeyEntity(RetrievableObject):
    count = 0
    _confidences = {}

    def __init__(self, pk, **kwargs):
        self.id = pk
        self.stemms = kwargs["stemms"]
        self.count = kwargs["count"]
        self.text = kwargs["text"]
        self._confidences = {"frequency": None,
                            "title": None,
                            "heuristical_mutual_information": None,
                            "statistical_mutual_information": None,
                            "nrelative_frequency": None,
#                            "thesaurus": None,
                            "pos": None
                           }
#        self.descriptor = None
#        if self.id in self.text.thesaurus:
#            self.descriptor = self.text.thesaurus[self.id]
        self.compute_confidence()
    
    def __unicode__(self):
        return u" ".join([unicode(t.main_occurrence) for t in self.stemms])
    
    def __repr__(self):
        return u"<KE %s>" % u" ".join([repr(t) for t in self.stemms]).decode("utf-8")
    
    def __iter__(self):
        return self.stemms.__iter__()
    
    def __len__(self):
        return len(self.stemms)

    def __getitem__(self, key):
        return self.stemms[key]

    def __eq__(self, other):
        """
        This is NOT for confidence or length comparison.
        For this use is_equal
        This is for content comparison.
        """
        return self.stemms == other.stemms

    def is_equal(self, other):
        """
        This is for confidence and length comparison.
        NOT for content comparison.
        """
        return self.confidence == other.confidence and len(self) == len(other)

    def __gt__(self, other):
        """
        We try here to define which from two keyentities competitor is the 
        best concentrate of information.
        (Remember that if an expression A is included in B, A is mathematicaly
        almost frequent than B.)
        Examples :
        - Ernesto Che Guevara, is more informative than "Che" or "Che 
        Guevara", even if "Che Guevara" is very more frequent.
        - "loi Création et Internet" is more concentrate, and so more informative,
        than "le projet de loi Création et Internet"
        - "ministère de la Culture" is more informative than "Culture" and "ministère"
        """
        #First of all, we check that both are competitors
        if not self in other and not other in self:
            raise ValueError("keyentities must be parent for this comparison.")
        log(u"Comparing '%s' and '%s'" % (unicode(self), unicode(other)), "GRAY")
        log(self._confidences, "GRAY")
        log(other._confidences, "GRAY")
        if not self.statistical_mutual_information_confidence() == other.statistical_mutual_information_confidence():
            return self.statistical_mutual_information_confidence() > other.statistical_mutual_information_confidence()
        elif not self.heuristical_mutual_information_confidence() == other.heuristical_mutual_information_confidence():
            return self.heuristical_mutual_information_confidence() > other.heuristical_mutual_information_confidence()
        elif not self.title_confidence() == other.title_confidence():
            return self.title_confidence() > other.title_confidence()
        elif not self.confidence == other.confidence:
            return self.confidence > other.confidence
        elif not len(self) == len(other):
            return len(self) > len(other)
        else: return False

    def __lt__(self, other):
        return other > self
#        if other.confidence > self.confidence: return True
#        elif other.confidence == self.confidence \
#             and len(other) > len(self): return True
#        else: return False

    def __le__(self, other):
        """
        Do not use.
        """
        raise NotImplementedError("This have no sens.")

    def __ge__(self, other):
        """
        Do not use.
        """
        raise NotImplementedError("This have no sens.")

    def index(self, key):
        return self.stemms.index(key)

    def __contains__(self, item):
        """
        Special behaviour if item is KeyEntity :
        determine if item is contained in self, or self in item.
        """
        if isinstance(item, KeyEntity):
            if len(item) > len(self): return False
            #item is shorter or equal
            if not item[0] in self: return False#The first element is not there
            idx = self.index(item[0])
            return item[:] == self[idx:idx+len(item)]
        else:
            return self.stemms.__contains__(item)

    @property
    def confidence(self):
        return self.collocation_confidence * self.keyconcept_confidence
#        return product([100] + [v for k, v in self._confidences.items()])
    
    @property
    def trigger_score(self):
        """
        Score used by trigger, may be the final confidence ?
        """
        return self.frequency_relative_pmi_confidence * self._confidences["pos"]
    
    @property
    def collocation_confidence(self):
        return ((self._confidences["heuristical_mutual_information"] 
                + self._confidences["statistical_mutual_information"]) / 2) \
                * self._confidences["pos"]
#                self._confidences["title"] * self._confidences["thesaurus"] \

    @property
    def keyconcept_confidence(self):
#        return ((self._confidences["nrelative_frequency"] + self._confidences["frequency"]) / 2 )
        return self._confidences["nrelative_frequency"]
    
    @property
    def frequency_relative_pmi_confidence(self):
        return self._confidences["statistical_mutual_information"] \
               * self._confidences["nrelative_frequency"]
    
    def compute_confidence(self):
        log(u"KeyEntity : %s" % unicode(self), "YELLOW")
        log(u"KeyEntity count : %d" % self.count, "GRAY")
        self._confidences["frequency"] = self.frequency_confidence()
        log(u"Frequency confidence : %f" % self._confidences["frequency"], "GRAY")
        self._confidences["nrelative_frequency"] = self.nrelative_frequency_confidence()
        log(u"Nrelative frequency confidence : %f" % self._confidences["nrelative_frequency"], "GRAY")
        self._confidences["title"] = self.title_confidence()
        log(u"Title confidence : %f" % self._confidences["title"], "GRAY")
        self._confidences["pos"] = self.pos_confidence()
        log(u"POS confidence : %f" % self._confidences["pos"], "GRAY")
        #As we have currently two PMI, we use the medium of each one
#        pmi_factor = math.sqrt(
#                     (self.heuristical_mutual_information_confidence() +
#                      self.statistical_mutual_information_confidence()) 
#                     /
#                     (2 * 
#                      self.heuristical_mutual_information_confidence() *
#                      self.statistical_mutual_information_confidence())
#                    )
        self._confidences["heuristical_mutual_information"] = self.heuristical_mutual_information_confidence()
        log(u"MI confidence : %f" % self._confidences["heuristical_mutual_information"], "GRAY")
        self._confidences["statistical_mutual_information"] = self.statistical_mutual_information_confidence()
        log(u"PMI confidence : %f" % self._confidences["statistical_mutual_information"], "GRAY")
#        self._confidences["thesaurus"] = self.thesaurus_confidence()
#        log(u"Thesaurus confidence : %f" % self._confidences["thesaurus"], "GRAY")
        log(u"Computed confidence : %f" % self.confidence, "BLUE")

    def frequency_confidence(self):
        """
        Lets define that a ngram of 10 for a text of 100 words
        means 1 of confidence, so 0.1
        """
        if self._confidences["frequency"] is None:
            self._confidences["frequency"] = 1.0 * self.count / len(self.text.words) / 0.1
        return self._confidences["frequency"]

    def nrelative_frequency_confidence(self):
        """
        This is the frequency of the entity relatively to the possible entity
        of its length.
        """
        ngram_possible = len(self.text) - len(self) + 1
        if self._confidences["nrelative_frequency"] is None:
            self._confidences["nrelative_frequency"] = 1.0 * self.count / ngram_possible
        return self._confidences["nrelative_frequency"]

    def title_confidence(self):
        """
        Define the probability of a ngram to be a title.
        Factor is for the confidence coex max.
        This may not have a negative effect, just positive :
        a title is a good candidate to be a collocation
        but this doesn't means that if it's not a title it's not a collocation.
        Two things have importance here : the proportion of title AND the number
        of titles.
        Ex. :
        - "Jérôme Bourreau" is "more" title than "Bourreau"
        - "projet de loi Création et Internet" is "less" title than "loi Création
        et Internet"
        """
        if self._confidences["title"] is None:
            confidence = 1
            factor = 3.0
            to_test = [n.main_occurrence for n in self if n.main_occurrence.has_meaning()]
            for item in to_test:
                # Proportion and occurrences
                if item.istitle(): confidence += factor / len(to_test) + 0.1
            self._confidences["title"] = confidence
        return self._confidences["title"]
    
    def pos_confidence(self):
        """
        Give a score linked to the POS of the subelements.
        """
        confidence = 0
        if self._confidences["pos"] is None:
            for stemm in self:
                if stemm.tag[:3] == "SBP": confidence += 2.5
                elif stemm.tag[:3] == "ADJ": confidence += 1.7
                elif stemm.tag[:3] == "SBC": confidence += 1.5
                elif stemm.main_occurrence.is_verb(): confidence += 1.2
                elif stemm.tag[:3] == ('ADV'): confidence += 1.0
                elif stemm.main_occurrence.is_avoir() \
                     or stemm.main_occurrence.is_etre(): confidence += 0.3
                else:
                    confidence += 0.1
            self._confidences["pos"] = confidence / len(self)
        return self._confidences["pos"]
    
    def heuristical_mutual_information_confidence(self):
        """
        Return the probability of all the terms of the ngram to appear together.
        The matter is to understand the dependance or independance of the terms.
        If just some terms appears out of this context, it may be normal (for
        exemple, a name, which appaers sometimes with both firstname and lastname
        and sometimes with just lastname). And if these terms appears many many
        times, but some others appears just in this context, the number doesn't
        count.
        If NO term appears out of this context, with have a good probability for
        a collocation.
        If each term appears out of this context, and specialy if this occurs
        often, we can doubt of this collocation candidate.
        Do we may consider the stop_words ?
        This may affect negativly and positivly the main confidence.
        """
        if self._confidences["heuristical_mutual_information"] is None:
            #We test just from interessant stemms, but we keep original position
            candidates = [(k, v) for k, v in enumerate(self) if v.is_valid()]
            alone_count = {}
            if len(self) == 1: return 1#Just one word, PMI doesn't make sense
            if len(candidates) == 0: return 0.1
            for position, stemm in candidates:
                alone_count[position] = 0
                neighbours = [(s, p - position) for p, s in enumerate(self) if not s is stemm]
                for tkn in stemm.occurrences:
                    if not tkn.is_neighbor(neighbours):
                        alone_count[position] += 1
            res = [v for k,v in alone_count.items()]
            if sum(res) == 0:
                return 3 * len(self)#We trust this collocation
            elif 0 in res:#Almost one important term appears just in this context
                return 2
            else:
                #We don't know, but not so confident...
                #The more the terms appears alone, the less we are confident
                #So the smaller is the coef
                return product([2.0 * len(self) / (len(self) + v) for v in res])
        return self._confidences["heuristical_mutual_information"]

    def statistical_mutual_information_confidence(self):
        """
        Number of occurrences of the ngram / number of ngrams possible
        /
        probability of each member of the ngram.
        """
        if self._confidences["statistical_mutual_information"] is None:
            if len(self) == 1: return 1.0#TODO : find better way for 1-grams...
            ngram_possible = len(self.text) - len(self) + 1
            members_probability = product([1.0 * s.count/len(self.text) for s in self])
            self._confidences["statistical_mutual_information"] = \
            math.log(1.0 * self.count / ngram_possible / members_probability)
        return self._confidences["statistical_mutual_information"]

    def thesaurus_confidence(self):
        """
        Try to find a descriptor in thesaurus, calculate levenshtein distance,
        and make a score.
        This may not be < 1, because if there is a descriptor, is a good point
        for the collocation, but if not, is doesn't means that this is not a 
        real collocation.
        """
        if self._confidences["thesaurus"] is None:
            if self.descriptor is None: return 1
            else:
                sorig = unicode(self)
                dorig = unicode(self.descriptor)
#                print sorig, dorig, len(sorig), lev(dorig, sorig), math.log(max(1, (len(sorig) - lev(dorig, sorig))))
                return math.log(max(1, (len(sorig) - lev(dorig, sorig))))
        return self._confidences["thesaurus"]

    def is_duplicate(self, KeyEntity):
        """
        Say two keyentities are duplicate if one is contained in the other.
        """
        return len(self) > len(KeyEntity) and KeyEntity in self or self in KeyEntity

    def merge(self, other):
        """
        Other is merged in self.
        Merging equal to say that other and self are the same KeyEntity, and self
        is the "delegate" of other.
        So (this is the case if other is smaller than self) each time other appears
        without the specific terms of self, we concider that is the same concept.
        So, we keep the highest frequency_confidence.
        """
        self._confidences["frequency"] = max(other._confidences["frequency"],
                                             self._confidences["frequency"])
        self._confidences["nrelative_frequency"] = max(other._confidences["nrelative_frequency"],
                                             self._confidences["nrelative_frequency"])

class Stemm(RetrievableObject):
    """
    Subpart of text, grouped by meaning (stem).
    This try to be the *core* meaning of a word, so many tokens can point to
    the same stemm.
    Should be renamed in Lemm, because we are talking about lemmatisation,
    not stemmatisation.
    """
    count = 0

    def __init__(self, pk, **kwargs):
        self.id = pk
        self.text = kwargs["text"]
        self.occurrences = []#Otherwise all the objects have the same reference
        self._main_occurrence = None
    
    def __unicode__(self):
        return unicode(self.id)
    
    def __repr__(self):
        return u"<Stemm '%s'>" % unicode(self.id)
    
    def __hash__(self):
        return self.id.__hash__()

    def __eq__(self, y):
        """
        WATCH OUT of the order you make the comparison between a Token and a Stemm :
        if stemm == token means that the comparison is on the stemme of bof
        if token == stemm means that the comparison is on the graph of both
        y could be a string or a Token or a Stemm
        """
        s = y
        if isinstance(y, Token):
            s = s.stemm #Will turn one time.
        elif isinstance(y, Stemm):
            s = s.id 
        return self.id == s

    def __ne__(self, y):
        return not self.__eq__(y)

    def istitle(self):
        return self.main_occurrence.istitle()
#        return all([o.istitle() for o in self.occurrences])
        #We try to use the majority instead of all (sometimes a proper name is also a common one)...
#        return [o.istitle() for o in self.occurrences].count(True) >= len(self.occurrences) / 2
    
    @property
    def tag(self):
        return self.main_occurrence.tag
    
    def is_valid(self):
        return self.main_occurrence.has_meaning()

    def is_valid_alone(self):
        return self.main_occurrence.has_meaning_alone()

    def has_interest(self):
        """
        Do we take it in count as potential KeyEntity?
        If count is less than x, but main_occurrence is a title, we try to keep it
        """
        return self.is_valid() and (self.count > 2 or self.istitle())

    def has_interest_alone(self):
        """
        Do we take it in count if alone ??
        If count is less than x, but main_occurrence is a title, we try to keep it
        """
        return self.is_valid_alone() and (self.count >= self.text.medium_word_count or self.istitle())

    @property
    def main_occurrence(self):#cache me
        if self._main_occurrence is None:
            self._main_occurrence = sorted(words_occurrences([t for t in self.occurrences]).iteritems(),
                      key=itemgetter(1), reverse=True)[0][0]
        return self._main_occurrence

    @property
    def count(self):
        """
        Number of occurrences of this stemm.
        """
        return len(self.occurrences)
