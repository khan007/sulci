#!/usr/bin/env python
# -*- coding:Utf-8 -*-

import os

#from collections import defaultdict
#from operator import itemgetter

from utils import load_file, save_to_file, log
from thesaurus import Trigger, Descriptor
from textmining import SemanticalTagger
from textminingutils import tokenize_text
from rules_templates import LemmatizerTemplateGenerator, RuleTemplate

class SemanticalTrainer(object):
    """
    Create and update triggers.
    """
    PENDING_EXT = ".pdg"
    VALID_EXT = ".trg"
    
    def __init__(self, thesaurus, pos_tagger):
        self.thesaurus = thesaurus
        self.pos_tagger = pos_tagger
        self._triggers = self.thesaurus.triggers
    
    def begin(self):
        """
        Make one trigger for each descriptor of the thesaurus.
        Have to be called one time at the begining, and that's all.
        """
        #TODO Add aliases...
        self._triggers = set()#do not use previous
        for idx in self.thesaurus:
            d = self.thesaurus[idx]
            t, created = Trigger.get_or_create(unicode(d), self, original=unicode(d), parent=self.thesaurus)
            t.connect(d, 1)
            self._triggers.add(t)
    
    def train(self, text, descriptors):
        """
        For the moment, descriptors are a string with "," separator.
        """
        validated_descriptors = set()
        #Retrieve descriptors
        for d in descriptors.split(","):
            #Get this tokenize_text out of my eyes !
            d = d.strip().replace(u"’", u"'")
            if not d == "":
                dsc, created = Descriptor.get_or_create(tokenize_text(d), self.thesaurus, original=tokenize_text(d))
                validated_descriptors.add(dsc)
                if created:
                    log(u"Lairning descriptor not in thesaurus : %s" % unicode(dsc), "RED")
        #Retrieve keytentities :
        S = SemanticalTagger(text, self.thesaurus, self.pos_tagger)
        current_triggers = set()
        for ke in S.keyentities:
            #Retrieve or create triggers
            t, created = Trigger.get_or_create(unicode(ke), self.thesaurus, original=unicode(ke), parent=self.thesaurus)
            self._triggers.add(t)
            current_triggers.add(t)
            t.current_score = ke.trigger_score
            #Attache descriptor
        log(u"Current triggers", "WHITE")
        log([unicode(d) for d in current_triggers], "YELLOW")
        log(u"Descriptors validated by human", "WHITE")
        log([unicode(d) for d in validated_descriptors], "YELLOW")
        #Descriptors calculated by SemanticalTagger
        calculated_descriptors = set(S.descriptors)
        log(u"Descriptors calculated", "WHITE")
        log([unicode(d) for d in calculated_descriptors], "YELLOW")
        #Descriptors that where tagged by humans, but not calculated
        false_negative = validated_descriptors.difference(calculated_descriptors)
        #Descriptors that where not tagged by humans, but where calculated
        false_positive = calculated_descriptors.difference(validated_descriptors)
        #Validated descriptors that where also calculated
        true_positive = calculated_descriptors.intersection(validated_descriptors)
        
        for d in true_positive:
            for t in current_triggers:
                if d in t:
                    t.connect(d, 2 + t.current_score)#trust the relation
                    log(u"Adding 2 to connection %s - %s" % (t, d), "YELLOW")
        
        for d in false_positive:
            for t in current_triggers:
                if d in t:
                    t.connect(d, -(1 + t.current_score))#untrust the relation
                    log(u"Removing 1 to connection %s - %s" % (t, d), "BLUE")
        
        for d in false_negative:
            for t in current_triggers:
                t.connect(d, t.current_score)#guess the relation
                log(u"Connecting %s and %s" % (t, d), "WHITE")
    
    def export(self, force):
        ext = force and self.VALID_EXT or self.PENDING_EXT
        final = []
        for t in self._triggers:
            t.clean_connections()
            e = t.export()
            if e:
                final.append(e)
        save_to_file("corpus/triggers%s" % ext, "\n".join(final) )

class LemmatizerTrainer(object):
    """
    Train the Lemmatizer.
    """
    def __init__(self, lemmatizer):
        self.lemmatizer = lemmatizer
    
    def train(self):
        final_rules = []
        #We need to have the right tag, here
        for token in self.lemmatizer.tokens:
            token.tag = token.verified_tag
        errors = self.get_errors()
        while errors:
            run_applied_rule = False
#            print unicode(t), t.verified_lemme
            for t in errors[:]:
                rules_candidates = []
                log(u"Error : %s, lemmatized %s instead of %s" % (unicode(t.original), t.lemme, t.verified_lemme), "WHITE")
                #Make rules candidates
                for tpl, _ in LemmatizerTemplateGenerator.register.items():
        #                    print "tpl", tpl
                    template, _ = LemmatizerTemplateGenerator.get_instance(tpl)
                    rules_candidates += template.make_rules(t)
                #Test the rules
                pondered_rules = self.test_rules(rules_candidates)
                rule_candidate, score = RuleTemplate.select_one(pondered_rules, len(self.lemmatizer))
                #Maybe the test "rule_candidate in final_rules" have to be done before...
                if rule_candidate and not rule_candidate in final_rules:#How to calculate the score min ?
                    template, _ = LemmatizerTemplateGenerator.get_instance(rule_candidate)
                    final_rules.append((rule_candidate, score))
                    #Apply the rule to the tokens
                    log(u"Applying rule %s (%s)" % (rule_candidate, score), "RED")
                    template.apply_rule(self.lemmatizer.tokens, rule_candidate)
                    run_applied_rule = True
                    #We have applied a rule, we can try another run
                    errors = self.get_errors()
                    break#break the for
            if run_applied_rule: continue#go back to while
            errors = None#Nothing applied, we stop here.
        LemmatizerTemplateGenerator.export(final_rules)
    
    def get_errors(self):
        final = []
        for token in self.lemmatizer.tokens:
            if token.lemme != token.verified_lemme:
                final.append(token)
        return final
    
    def test_rules(self, rules_candidates):
        pondered_rules = []
        for rule in rules_candidates:
            pondered_rules.append(self.test_rule(rule))
        return pondered_rules
    
    def test_rule(self, rule):
        template, _ = LemmatizerTemplateGenerator.get_instance(rule)
        bad = 0
        good = 0
        for ttk in self.lemmatizer.tokens:
            test = template.test_rule(ttk, rule)
            if test == 1:
                good += 1
            elif test == -1:
                bad += 1
        log(u"%s g: %d b : %d" % (rule, good, bad), "GRAY")
        return rule, good, bad
    